"""Mari — document link extraction (LINEAGE-ROLLUP-CONTRACT.md §2).

Three edge rels written into the existing `edges` table:

- `references` — `#N` text refs in pr/issue/commit bodies → the same source's
  `issues/N` or `pulls/N` document (merge-commit "(#N)" therefore lands as
  commit → PR). Fixes/closes phrasing keeps the same rel.
- `links_to`   — markdown relative links between page docs of the same source,
  resolved (./, ../, repo-root /) against the linking doc's source_path;
  #fragments and query strings stripped.
- `similar`    — pgvector cosine over document embeddings ≥ 0.78, top 3 per
  doc, page↔page and page↔(pr|issue) pairs only, deduped both directions,
  capped 1000 per source.

Idempotent: unique index on (from_doc, to_doc, rel) (init.sql) + ON CONFLICT
DO NOTHING — re-running extraction adds nothing.

Import-cycle-free by design: talks to Postgres directly via MARI_DB (same
default as db.py); imports no app modules.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row
from mari_components.knowledge import (
    DEFAULT_SIMILARITY_LIMIT, DEFAULT_SIMILARITY_THRESHOLD,
    derive_links, extract_explicit_links,
)

from mari_server.persistence.postgres import connection as postgres

SIM_THRESHOLD = DEFAULT_SIMILARITY_THRESHOLD
SIM_TOP_K = DEFAULT_SIMILARITY_LIMIT
SIM_CAP_PER_SOURCE = 1000

def _conn():
    return postgres.connect()


def graph(project_id: int) -> list[dict]:
    with _conn() as conn:
        return conn.execute("""
          WITH degree AS (
            SELECT doc, sum(inb) AS inbound, sum(outb) AS outbound FROM (
              SELECT to_doc AS doc, 1 AS inb, 0 AS outb FROM edges WHERE project_id = %s
              UNION ALL
              SELECT from_doc AS doc, 0 AS inb, 1 AS outb FROM edges WHERE project_id = %s
            ) x GROUP BY doc
          )
          SELECT d.id, d.source, d.external_id, d.title, d.author, d.updated_src,
                 d.created_src, d.graph_x, d.graph_y, d.graph_icon, d.graph_meta,
                 d.kind, d.source_path, d.source_id, s.kind AS src_kind,
                 s.config->>'repo' AS repo,
                 array_remove(array_agg(DISTINCT t.tag), NULL) AS tags,
                 coalesce(max(g.inbound), 0)::int AS inbound,
                 coalesce(max(g.outbound), 0)::int AS outbound
            FROM documents d
            LEFT JOIN tags t ON t.project_id = d.project_id AND t.document_id = d.id
            LEFT JOIN sources s ON s.project_id = d.project_id AND s.id = d.source_id
            LEFT JOIN degree g ON g.doc = d.id
           WHERE d.project_id = %s
           GROUP BY d.id, s.kind, s.config ORDER BY d.id""",
            (project_id, project_id, project_id),
        ).fetchall()


def graph_edges(project_id: int) -> list[dict]:
    with _conn() as conn:
        return conn.execute("""
          SELECT e.id, f.external_id AS from_id, t.external_id AS to_id,
                 e.rel, e.created_at, e.curve, e.meta
            FROM edges e JOIN documents f ON f.id = e.from_doc
            JOIN documents t ON t.id = e.to_doc
           WHERE e.project_id = %s AND f.project_id = %s AND t.project_id = %s
           ORDER BY e.id""", (project_id, project_id, project_id),
        ).fetchall()


def graph_stats(project_id: int) -> tuple[dict, int, list[dict], list[dict]]:
    with _conn() as conn:
        summary = conn.execute("""
          SELECT count(*) AS docs,
                 count(*) FILTER (WHERE d.updated_src <
                   (SELECT max(updated_src) FROM documents WHERE project_id = %s) - interval '45 days'
                   OR EXISTS (SELECT 1 FROM tags t WHERE t.project_id = %s
                              AND t.document_id = d.id
                              AND t.tag IN ('stale','needs-review'))) AS stale,
                 count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM edges e WHERE e.project_id = %s
                     AND (e.from_doc = d.id OR e.to_doc = d.id))) AS orphans,
                 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM tags t
                   WHERE t.project_id = %s AND t.document_id = d.id
                     AND t.tag = 'customer-facing') AND NOT EXISTS (
                   SELECT 1 FROM edges e WHERE e.project_id = %s AND e.rel = 'translates'
                     AND (e.from_doc = d.id OR e.to_doc = d.id))) AS untranslated,
                 count(*) FILTER (WHERE d.author = '' OR d.author = 'CI') AS unowned
            FROM documents d WHERE d.project_id = %s""",
            (project_id, project_id, project_id, project_id, project_id, project_id),
        ).fetchone()
        contradictions = conn.execute(
            "SELECT count(*) AS n FROM edges WHERE project_id = %s AND rel = 'contradicts'",
            (project_id,),
        ).fetchone()["n"]
        cited = conn.execute("""
          SELECT d.title, d.id, count(*) AS inbound FROM edges e
          JOIN documents d ON d.id = e.to_doc
          WHERE e.project_id = %s AND d.project_id = %s
          GROUP BY d.id ORDER BY inbound DESC, d.id LIMIT 3""",
            (project_id, project_id),
        ).fetchall()
        activity = conn.execute("""
          SELECT * FROM (SELECT occurred_at::date AS day, count(*) AS n FROM events
          WHERE project_id = %s GROUP BY 1 ORDER BY 1 DESC LIMIT 60) x ORDER BY day""",
            (project_id,),
        ).fetchall()
    return summary, int(contradictions), cited, activity


# How many edges go in one INSERT. Large enough that a full extraction is a
# handful of statements, small enough that one statement's parameter list stays
# a reasonable size (4 parameters per row).
_INSERT_BATCH = 500


def _insert_edges(conn, project_id: int,
                  triples: list[tuple[int, int, str, dict]]) -> int:
    """Insert (from, to, rel, meta) edges; returns how many were actually new.

    Batched (SQL-4). This issued one INSERT … RETURNING per edge, so a source
    hitting the 1000-edge similarity cap made 1000 round trips to write 1000
    rows — the round trips, not the writes, were the cost. Same rows, same
    ON CONFLICT DO NOTHING, same count of genuinely-new edges; two orders of
    magnitude fewer statements.

    Duplicates within one call are collapsed first. ON CONFLICT DO NOTHING
    tolerates them, but the same pair arriving twice from two extractors would
    otherwise be counted once and inserted once, which reads as a lost edge."""
    seen: dict[tuple[int, int, str], dict] = {}
    for src, dst, rel, meta in triples:
        if src != dst:
            seen.setdefault((src, dst, rel), meta)
    rows = [(project_id, src, dst, rel, psycopg.types.json.Json(meta))
            for (src, dst, rel), meta in seen.items()]
    created = 0
    for i in range(0, len(rows), _INSERT_BATCH):
        batch = rows[i:i + _INSERT_BATCH]
        values = ", ".join(["(%s, %s, %s, %s, 0, 0, %s, CURRENT_DATE)"] * len(batch))
        args = [field for row in batch for field in row]
        created += len(conn.execute(
            f"""INSERT INTO edges (project_id, from_doc, to_doc, rel, day, curve, meta, created_at)
                VALUES {values}
                ON CONFLICT (from_doc, to_doc, rel) DO NOTHING
                RETURNING id""", args).fetchall())
    return created


# ————————————————— references (#N) —————————————————


def _extract_references(conn, source_id: int, project_id: int,
                        doc_ids: list[int] | None) -> int:
    """pr/issue/commit bodies containing #N → edge to issues/N | pulls/N doc."""
    where, args = "d.source_id = %s AND d.kind IN ('pr','issue','commit')", [source_id]
    if doc_ids is not None:
        where += " AND d.id = ANY(%s)"
        args.append(doc_ids)
    docs = conn.execute(
        f"SELECT d.id, d.body, d.source_path FROM documents d WHERE {where}", args).fetchall()
    if not docs:
        return 0
    # number → doc id map for this source's issues/PRs
    num_map: dict[str, str] = {}
    for r in conn.execute(
            """SELECT id, source_path FROM documents
               WHERE source_id = %s AND (source_path LIKE 'issues/%%' OR source_path LIKE 'pulls/%%')""",
            (source_id,)).fetchall():
        tail = r["source_path"].split("/", 1)[1]
        if tail.isdigit():
            num_map[tail] = str(r["id"])
    triples = []
    for d in docs:
        for link in extract_explicit_links(
                str(d["id"]), d["source_path"], d["body"] or "", num_map):
            if link.kind == "references":
                triples.append((d["id"], int(link.target_id), "references", {}))
    return _insert_edges(conn, project_id, triples)


# ————————————————— links_to (markdown relative links) —————————————————


def _extract_links_to(conn, source_id: int, project_id: int,
                      doc_ids: list[int] | None) -> int:
    where, args = "d.source_id = %s AND d.kind = 'page'", [source_id]
    if doc_ids is not None:
        where += " AND d.id = ANY(%s)"
        args.append(doc_ids)
    docs = conn.execute(
        f"SELECT d.id, d.body, d.source_path FROM documents d WHERE {where}", args).fetchall()
    if not docs:
        return 0
    path_map = {r["source_path"]: str(r["id"]) for r in conn.execute(
        "SELECT id, source_path FROM documents WHERE source_id = %s AND kind = 'page'",
        (source_id,)).fetchall()}
    triples = []
    for d in docs:
        for link in extract_explicit_links(
                str(d["id"]), d["source_path"], d["body"] or "", path_map):
            if link.kind == "links_to":
                triples.append((d["id"], int(link.target_id), "links_to", {}))
    return _insert_edges(conn, project_id, triples)


# ————————————————— similar (pgvector cosine) —————————————————


def _extract_similar(conn, source_id: int, project_id: int,
                     doc_ids: list[int] | None) -> int:
    """Top-K cosine neighbors ≥ threshold; page↔page and page↔(pr|issue) only.
    Incremental runs restrict the LEFT side to changed docs but always compare
    against all docs of the source. Deduped both directions; capped per source.

    On the cost of this (SQL-4): the SQL is unchanged, because it was already
    written in the one shape a vector index can serve — `JOIN LATERAL … ORDER BY
    a.embedding <=> b.embedding LIMIT k`. What it lacked was the index.
    `documents_embedding_hnsw_idx` (init.sql) now gives the planner the option,
    and it takes it once ranking the source's documents by distance costs more
    than the alternative — which is the case this finding is about, a source
    large enough for the inner sort to dominate. On a workspace whose documents
    are spread thinly across many sources the planner will still narrow by
    `source_id` and sort, and that is the cheaper plan there; the point is that
    the O(n²) sort is no longer the only plan available.

    `ef_search` is raised for this statement because when the HNSW path IS
    chosen, the inner query's kind/source filters are applied to rows the index
    streams out in distance order — too small a search list and a qualifying
    neighbour a few places down the ranking is never reached. 100 leaves ample
    room above the top 3 this asks for."""
    conn.execute("SET LOCAL hnsw.ef_search = 100")
    where, args = ("a.source_id = %(sid)s AND a.embedding IS NOT NULL "
                   "AND a.kind IN ('page','pr','issue')"), {"sid": source_id, "k": SIM_TOP_K}
    if doc_ids is not None:
        where += " AND a.id = ANY(%(ids)s)"
        args["ids"] = doc_ids
    rows = conn.execute(f"""
        SELECT a.id AS src, b.id AS dst, b.sim
        FROM documents a
        JOIN LATERAL (
          SELECT b.id, 1 - (a.embedding <=> b.embedding) AS sim
          FROM documents b
          WHERE b.source_id = a.source_id AND b.id <> a.id AND b.embedding IS NOT NULL
            AND ((a.kind = 'page' AND b.kind IN ('page','pr','issue'))
                 OR (b.kind = 'page' AND a.kind IN ('pr','issue')))
          ORDER BY a.embedding <=> b.embedding
          LIMIT %(k)s
        ) b ON b.sim >= {SIM_THRESHOLD}
        WHERE {where}
        ORDER BY b.sim DESC""", args).fetchall()
    # Apply the reusable threshold/top-k policy, then dedupe both directions.
    by_source: dict[int, dict[int, float]] = {}
    for row in rows:
        by_source.setdefault(int(row["src"]), {})[int(row["dst"])] = float(row["sim"])
    pairs: dict[tuple[int, int], float] = {}
    for source, candidates in by_source.items():
        selected = derive_links(
            str(source), (str(candidate) for candidate in candidates),
            score=lambda _source, target: candidates[int(target)],
            threshold=SIM_THRESHOLD, limit=SIM_TOP_K,
        )
        for link in selected:
            target = int(link.target_id)
            key = (min(source, target), max(source, target))
            pairs[key] = max(pairs.get(key, 0.0), link.score)
    existing = conn.execute(
        """SELECT count(*) AS n FROM edges e
           JOIN documents d ON d.id = e.from_doc
           WHERE e.rel = 'similar' AND d.source_id = %s""", (source_id,)).fetchone()["n"]
    budget = max(SIM_CAP_PER_SOURCE - existing, 0)
    ranked = sorted(pairs.items(), key=lambda kv: -kv[1])[:budget]
    triples = [(a, b, "similar", {"sim": round(sim, 4)}) for (a, b), sim in ranked]
    return _insert_edges(conn, project_id, triples)


# ————————————————— entry points —————————————————


def extract(source_id: int, doc_ids: list[int] | None = None,
            *, project_id: int | None = None) -> dict[str, int]:
    """Extract link edges for one source. doc_ids=None → full pass over the
    source; a doc-id list → incremental (those docs only; similarity for those
    docs is still computed against the whole source). Returns per-rel counts
    of newly created edges. Idempotent — a re-run creates 0."""
    if doc_ids is not None and not doc_ids:
        return {"references": 0, "links_to": 0, "similar": 0}
    with _conn() as conn:
        source = conn.execute(
            "SELECT project_id FROM sources WHERE id = %s", (source_id,)).fetchone()
        if not source or source.get("project_id") is None:
            raise ValueError(f"source {source_id} has no project")
        source_project_id = int(source["project_id"])
        if project_id is not None and source_project_id != int(project_id):
            raise PermissionError("source does not belong to the active project")
        counts = {
            "references": _extract_references(conn, source_id, source_project_id, doc_ids),
            "links_to": _extract_links_to(conn, source_id, source_project_id, doc_ids),
            "similar": _extract_similar(conn, source_id, source_project_id, doc_ids),
        }
        conn.commit()
    return counts


def extract_all(project_id: int) -> int:
    """Full extraction for every source that has documents. Returns total edges created."""
    with _conn() as conn:
        sids = [r["source_id"] for r in conn.execute(
            """SELECT DISTINCT source_id FROM documents
               WHERE project_id = %s AND source_id IS NOT NULL""",
            (project_id,)).fetchall()]
    return sum(sum(extract(sid, project_id=project_id).values()) for sid in sids)

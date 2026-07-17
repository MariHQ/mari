"""Mari Cloud — document link extraction (LINEAGE-ROLLUP-CONTRACT.md §2).

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
import posixpath
import re

import psycopg
from psycopg.rows import dict_row

DB_URL = os.environ.get("MARI_DB", "postgresql://localhost/mari_cloud")

SIM_THRESHOLD = 0.78
SIM_TOP_K = 3
SIM_CAP_PER_SOURCE = 1000

# "#123" not preceded by a word char, '/', or '&' (avoids URL fragments like
# /pull/12#issuecomment-..., paths, and HTML entities such as &#39;).
REF_RE = re.compile(r"(?<![\w/&#])#(\d+)\b")

# [text](target) / [text](target "title") — target captured without ')' or space.
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")

_SKIP_SCHEMES = ("http://", "https://", "mailto:", "data:", "ftp://", "tel:")


def _conn():
    return psycopg.connect(DB_URL, row_factory=dict_row)


def _insert_edges(conn, triples: list[tuple[int, int, str, dict]]) -> int:
    """Insert (from, to, rel, meta) edges; returns how many were actually new."""
    created = 0
    for src, dst, rel, meta in triples:
        if src == dst:
            continue
        row = conn.execute(
            """INSERT INTO edges (from_doc, to_doc, rel, day, curve, meta, created_at)
               VALUES (%s, %s, %s, 0, 0, %s, CURRENT_DATE)
               ON CONFLICT (from_doc, to_doc, rel) DO NOTHING
               RETURNING id""",
            (src, dst, rel, psycopg.types.json.Json(meta))).fetchone()
        if row:
            created += 1
    return created


# ————————————————— references (#N) —————————————————


def _extract_references(conn, source_id: int, doc_ids: list[int] | None) -> int:
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
    num_map: dict[int, int] = {}
    for r in conn.execute(
            """SELECT id, source_path FROM documents
               WHERE source_id = %s AND (source_path LIKE 'issues/%%' OR source_path LIKE 'pulls/%%')""",
            (source_id,)).fetchall():
        tail = r["source_path"].split("/", 1)[1]
        if tail.isdigit():
            num_map[int(tail)] = r["id"]
    triples = []
    for d in docs:
        seen: set[int] = set()
        for m in REF_RE.finditer(d["body"] or ""):
            n = int(m.group(1))
            target = num_map.get(n)
            if target and target != d["id"] and n not in seen:
                seen.add(n)
                triples.append((d["id"], target, "references", {"ref": f"#{n}"}))
    return _insert_edges(conn, triples)


# ————————————————— links_to (markdown relative links) —————————————————


def _resolve(base_path: str, target: str) -> str | None:
    """Resolve a markdown link target against the linking doc's source_path."""
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target or target.lower().startswith(_SKIP_SCHEMES):
        return None
    if target.startswith("/"):  # repo-root absolute
        resolved = target.lstrip("/")
    else:
        resolved = posixpath.join(posixpath.dirname(base_path), target)
    resolved = posixpath.normpath(resolved)
    if resolved.startswith(".."):
        return None
    return resolved


def _extract_links_to(conn, source_id: int, doc_ids: list[int] | None) -> int:
    where, args = "d.source_id = %s AND d.kind = 'page'", [source_id]
    if doc_ids is not None:
        where += " AND d.id = ANY(%s)"
        args.append(doc_ids)
    docs = conn.execute(
        f"SELECT d.id, d.body, d.source_path FROM documents d WHERE {where}", args).fetchall()
    if not docs:
        return 0
    path_map = {r["source_path"]: r["id"] for r in conn.execute(
        "SELECT id, source_path FROM documents WHERE source_id = %s AND kind = 'page'",
        (source_id,)).fetchall()}
    triples = []
    for d in docs:
        seen: set[int] = set()
        for m in MD_LINK_RE.finditer(d["body"] or ""):
            resolved = _resolve(d["source_path"], m.group(1))
            if not resolved:
                continue
            target = (path_map.get(resolved)
                      or path_map.get(resolved + ".md")
                      or path_map.get(posixpath.join(resolved, "README.md")))
            if target and target != d["id"] and target not in seen:
                seen.add(target)
                triples.append((d["id"], target, "links_to", {"href": m.group(1)}))
    return _insert_edges(conn, triples)


# ————————————————— similar (pgvector cosine) —————————————————


def _extract_similar(conn, source_id: int, doc_ids: list[int] | None) -> int:
    """Top-K cosine neighbors ≥ threshold; page↔page and page↔(pr|issue) only.
    Incremental runs restrict the LEFT side to changed docs but always compare
    against all docs of the source. Deduped both directions; capped per source."""
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
    # dedupe both directions: canonical (min, max), keep best score
    pairs: dict[tuple[int, int], float] = {}
    for r in rows:
        key = (min(r["src"], r["dst"]), max(r["src"], r["dst"]))
        pairs[key] = max(pairs.get(key, 0.0), float(r["sim"]))
    existing = conn.execute(
        """SELECT count(*) AS n FROM edges e
           JOIN documents d ON d.id = e.from_doc
           WHERE e.rel = 'similar' AND d.source_id = %s""", (source_id,)).fetchone()["n"]
    budget = max(SIM_CAP_PER_SOURCE - existing, 0)
    ranked = sorted(pairs.items(), key=lambda kv: -kv[1])[:budget]
    triples = [(a, b, "similar", {"sim": round(sim, 4)}) for (a, b), sim in ranked]
    return _insert_edges(conn, triples)


# ————————————————— entry points —————————————————


def extract(source_id: int, doc_ids: list[int] | None = None) -> dict[str, int]:
    """Extract link edges for one source. doc_ids=None → full pass over the
    source; a doc-id list → incremental (those docs only; similarity for those
    docs is still computed against the whole source). Returns per-rel counts
    of newly created edges. Idempotent — a re-run creates 0."""
    if doc_ids is not None and not doc_ids:
        return {"references": 0, "links_to": 0, "similar": 0}
    with _conn() as conn:
        counts = {
            "references": _extract_references(conn, source_id, doc_ids),
            "links_to": _extract_links_to(conn, source_id, doc_ids),
            "similar": _extract_similar(conn, source_id, doc_ids),
        }
        conn.commit()
    return counts


def extract_all() -> int:
    """Full extraction for every source that has documents. Returns total edges created."""
    with _conn() as conn:
        sids = [r["source_id"] for r in conn.execute(
            "SELECT DISTINCT source_id FROM documents WHERE source_id IS NOT NULL").fetchall()]
    return sum(sum(extract(sid).values()) for sid in sids)

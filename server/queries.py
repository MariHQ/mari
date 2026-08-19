"""Mari — GraphQL Query root, hybrid search, and lineage layout.

DESIGN.md §4–§5: hybrid search = tsvector rank + pgvector cosine, boosted by
tag weights (tag_definitions.search_weight).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
import time

import strawberry
import numpy as np
from strawberry.scalars import JSON

import config
import github
import ingest
import llm
import retrieval
from db import actor_name, exec_, jload, q, q1
from excerpt import excerpt
from gqltypes import (
    ActivityBucket, ActivityItem, ApiKey, ApprovedAnswer,
    AuditDetail, AuditEvent, AuditFinding, AuditRun, Change, ChatMessage,
    ChatSession, Checkpoint, Decision, DigestImpact, DigestTopic, DigestWhere,
    DocHistory, Document, DocumentTemplate, Fact, FactContradiction, Finding,
    FreshnessRow, GithubRepo, GithubTeamSync, GlossaryCandidate, GlossaryTerm,
    GraphStats, GraphView, InsightStats, LineageEdge, LineageNode, McpServer,
    Member, Notification, Provisioning, ReadabilityRow, RelatedDoc, Release, Setting, Site,
    SiteFeature, SiteThemePreset, SourcePulse, StyleGuide, StyleRule, SyncEvent,
    SyncStatus, TagDef, Task, TaskSummary, TopCited, UploadFile, UploadManifest,
    VoiceLayer, Workflow, WorkflowRun, Workspace,
)

# ————————————————— lineage layout (LINEAGE-DESIGN.md §3.3) —————————————————

LANES = {"github": (0.14, 0.30), "slack": (0.32, 0.46), "docs": (0.48, 0.66),
         "notion": (0.68, 0.80)}
LANE_OTHER = (0.82, 0.90)
SOURCE_ICONS = {"github": "github", "slack": "slack", "docs": "doc", "notion": "notion"}


def _iso_date_arg(value: str | None) -> str | None:
    """An ISO date (YYYY-MM-DD) argument, or None. Anything that is not a date
    is rejected rather than silently ignored: a range control whose bound the
    server quietly dropped would relabel numbers it did not change."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v[:10]).isoformat()
    except ValueError as e:
        raise ValueError(f"{value!r} is not an ISO date (YYYY-MM-DD)") from e


def _hash01(s: str) -> float:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16) % 1000 / 1000


def layout_nodes(rows: list[dict]) -> dict[int, tuple[float, float]]:
    """Deterministic auto-layout: x = time, y = source lane + stable hash offset,
    then a collision pass. Pinned nodes keep graph_x/graph_y verbatim."""
    dates = [r["updated_src"] for r in rows if r["updated_src"]]
    lo, hi = (min(dates), max(dates)) if dates else (dt.date.today(), dt.date.today())
    span = max((hi - lo).days, 1)
    pos, pinned = {}, set()
    for r in rows:
        if r["graph_x"] is not None:
            pos[r["id"]] = (float(r["graph_x"]), float(r["graph_y"] or 0.5))
            pinned.add(r["id"])
        else:
            x = 0.24 + ((r["updated_src"] or lo) - lo).days / span * (0.86 - 0.24)
            y0, y1 = LANES.get(r["source"], LANE_OTHER)
            pos[r["id"]] = (x, y0 + _hash01(r["external_id"]) * (y1 - y0))
    # Collision pass: move each colliding unpinned node (id order) to the nearest
    # free y on a fine grid, clear of everything already placed. Deterministic;
    # pinned-pinned overlaps stand (user's choice).
    placed = sorted(pinned)
    for b in sorted(k for k in pos if k not in pinned):
        bx, by = pos[b]

        def free(yv: float) -> bool:  # 1e-9 slack absorbs float grid error
            return all(abs(pos[a][0] - bx) >= 0.16 - 1e-9 or abs(pos[a][1] - yv) >= 0.09 - 1e-9
                       for a in placed)

        if not free(by):
            cands = sorted((0.13 + k * 0.01 for k in range(78)), key=lambda v: (abs(v - by), v))
            pos[b] = (bx, next((c for c in cands if free(c)), by))
        placed.append(b)
    return pos


def classify_node(row: dict) -> tuple[str, str]:
    """LINEAGE-ROLLUP-CONTRACT.md §1 — (docKind, group) from source_path +
    source kind. Legacy seed docs (no source_id) → ("seed", ""). Markdown
    pages are never grouped; commits/prs/issues group per repo."""
    if row.get("source_id") is None:
        return "seed", ""
    sp = row.get("source_path") or ""
    kind = row.get("kind") or "page"
    if sp.startswith("issues/"):
        doc_kind = "issue"
    elif sp.startswith("pulls/"):
        doc_kind = "pr"
    elif sp.startswith("commits/"):
        doc_kind = "commit"
    elif kind in ("answer", "decision"):
        doc_kind = kind
    else:
        doc_kind = "page"
    if doc_kind in ("commit", "pr", "issue") and row.get("src_kind") == "github" and row.get("repo"):
        return doc_kind, f"gh:{row['repo']}:{doc_kind}s"
    return doc_kind, ""


def _doc(row: dict, watched: bool = False) -> Document:
    return Document(
        id=row["id"], source=row["source"], title=row["title"],
        # The card's sentence, built from the body when the row carries one
        # (excerpt.py). Rows ingested before excerpt existed stored the raw
        # first 180 characters, Markdown preamble and all; recomputing here
        # renders them the same as new rows without a backfill.
        snippet=excerpt(row.get("body"), row["title"]) if row.get("body") else row["snippet"],
        body=row.get("body", ""), kind=row.get("kind", "page"), author=row["author"], author_initials=row["author_initials"],
        # ISO 8601, NOT a display string. The console formats dates itself
        # (components/tokens/format.ts fmtDate) and sorts columns on the raw
        # value, so "Jul 8, 2026" would both double-format and sort
        # alphabetically. The API returns data; the client renders it.
        date=row["updated_src"].isoformat() if row["updated_src"] else "",
        tags=row.get("tags") or [], watched=watched,
    )


# A page of documents ordered by recency. `d.id DESC` is not decoration:
# updated_src is a DATE, so a corpus ingested in one sync shares one value
# across thousands of rows and the sort between them is whatever the plan
# happens to produce. Paging an unstable sort duplicates some rows and skips
# others (SRCH-2), and the reader has no way to tell.
DOC_SQL = """
  SELECT d.id, d.source, d.external_id, d.title, d.snippet, d.body, d.author,
         d.author_initials, d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags
  FROM documents d LEFT JOIN tags t ON t.document_id = d.id
  {where}
  GROUP BY d.id ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
"""

# ————————————————— hybrid search ranking constants —————————————————

# ONE similarity threshold, used both to admit a document and to score it
# (SRCH-7). There used to be two — inclusion at 0.45, scoring at 0.35 — which
# meant a document scoring 0.40 was dropped from a result set it would have
# ranked inside of. Whichever number is right, a document that scores has to be
# a document that appears.
SIM_FLOOR = 0.35

# How deep the approximate-nearest-neighbour probe goes before the exact
# scoring pass. This is the one place hybrid search is approximate, and it is a
# deliberate trade: "every document whose cosine similarity exceeds a threshold"
# has no bound and no index that can answer it, so it can only ever be a scan of
# the whole corpus. "The N nearest, then filter by threshold" is what an HNSW
# index answers in milliseconds. A query matching more than 400 documents purely
# by meaning — no keyword, no title match — returns the 400 closest.
#
# The count and the rows read the same CTE, so they are approximate in exactly
# the same way and can never disagree with each other.
ANN_CANDIDATES = 400

# The largest page `search` will return. Without a ceiling, search(query:"",
# k:1000000) serves the entire corpus — bodies included — in one response
# (SRCH-6). The console pages by re-issuing with a larger k, so this is a
# clamp rather than an error: a caller asking past the end of the corpus should
# get the end of the corpus, not a failure.
MAX_K = 500

# LIKE/ILIKE metacharacters. Unescaped, a query of "%" matches every row and
# `searchTotal` reports the whole corpus as a match for one character (SRCH-6).
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def like_pattern(query: str) -> str:
    """`query` as a LIKE pattern that matches it literally. Backslash is the
    default LIKE escape character in Postgres, so escaping it first (then % and
    _) needs no ESCAPE clause."""
    return f"%{query.translate(_LIKE_ESCAPE)}%"


# The scoring CTE, shared by the page query and the count query so a total can
# never describe a different match set from the rows it is counting.
#
# `doc_ann`/`chunk_ann` exist so the semantic half can use an index. pgvector's
# HNSW indexes (init.sql) answer exactly one shape — `ORDER BY embedding <=>
# vec LIMIT n` — and the previous CTE had no FROM filter at all, so it computed
# a distance for every document plus a correlated max() over every chunk of
# every document, on every keystroke, and then discarded almost all of it
# (SRCH-5). `candidates` narrows to rows that can plausibly match before any
# per-row work happens; `scored` computes the exact scores over that set.
SCORED_CTE = """
  WITH doc_ann AS (
    SELECT id, 1 - (embedding <=> %(vec)s::vector) AS sim
    FROM documents
    WHERE %(has_vec)s AND embedding IS NOT NULL
    ORDER BY embedding <=> %(vec)s::vector
    LIMIT %(ann)s
  ),
  chunk_ann AS (
    SELECT document_id, max(sim) AS sim FROM (
      SELECT document_id, 1 - (embedding <=> %(vec)s::vector) AS sim
      FROM chunks
      WHERE %(has_vec)s AND embedding IS NOT NULL
      ORDER BY embedding <=> %(vec)s::vector
      LIMIT %(ann)s
    ) c GROUP BY document_id
  ),
  candidates AS (
    SELECT d.id FROM documents d
     WHERE d.search_vec @@ plainto_tsquery('english', %(q)s)
        OR d.title ILIKE %(like)s
    UNION
    SELECT id FROM doc_ann WHERE sim > %(floor)s
    UNION
    SELECT document_id FROM chunk_ann WHERE sim > %(floor)s
  ),
  scored AS (
    SELECT d.id,
           ts_rank(d.search_vec, plainto_tsquery('english', %(q)s)) AS kw,
           -- semantic score: best chunk match when chunks exist, else doc embedding
           greatest(
             CASE WHEN d.embedding IS NULL OR %(has_vec)s = false THEN 0
                  ELSE 1 - (d.embedding <=> %(vec)s::vector) END,
             CASE WHEN %(has_vec)s = false THEN 0
                  ELSE coalesce((SELECT max(1 - (c.embedding <=> %(vec)s::vector))
                                 FROM chunks c
                                 WHERE c.document_id = d.id AND c.embedding IS NOT NULL), 0) END
           ) AS sim,
           coalesce((SELECT max(td.search_weight) FROM tags tg
                     JOIN tag_definitions td ON td.tag = tg.tag
                     WHERE tg.document_id = d.id), 1.0) AS boost
    FROM candidates cd JOIN documents d ON d.id = cd.id
  )
"""

MATCHES = "WHERE s.kw > 0 OR s.sim > %(floor)s OR d.title ILIKE %(like)s"

# `d.id DESC` is the stable tiebreaker (SRCH-2). Without it the sort has no
# total order — updated_src is a DATE, and every title-only match scores
# exactly 0 — so two pages of the same result set can contain the same document
# twice and omit another entirely.
HYBRID_SQL = SCORED_CTE + """
  SELECT d.id, d.source, d.title, d.snippet, d.body, d.author, d.author_initials,
         d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags,
         (s.kw * 2.0 + greatest(s.sim - %(floor)s, 0) * 3.0) * s.boost AS score
  FROM scored s
  JOIN documents d ON d.id = s.id
  LEFT JOIN tags t ON t.document_id = d.id
""" + MATCHES + """
  GROUP BY d.id, s.kw, s.sim, s.boost
  ORDER BY score DESC, d.updated_src DESC NULLS LAST, d.id DESC
  LIMIT %(k)s OFFSET %(offset)s
"""

HYBRID_COUNT_SQL = SCORED_CTE + """
  SELECT count(*) AS n
  FROM scored s
  JOIN documents d ON d.id = s.id
""" + MATCHES


# ————————————————— one embedding per query (SRCH-1) —————————————————
#
# `search` and `searchTotal` are separate resolvers that the console asks for in
# ONE GraphQL document, and each used to call llm.embed itself. llm.embed
# returns None when ollama is slow or down, which flips has_vec false — so if
# one call succeeded and the other timed out, the count described a keyword
# match set while the rows described a hybrid one, and the page said "showing 10
# of 3".
#
# This cache makes the mismatch impossible rather than unlikely: the FIRST
# resolver to ask for a query's vector decides, and the second reuses that
# decision — including the decision that there is no vector. Two resolvers in
# one request can no longer disagree about whether this search was semantic.
# The TTL keeps a transient ollama outage from being remembered all day.
_VEC_TTL_SECONDS = 120.0
_VEC_CACHE_MAX = 128
_vec_cache: dict[str, tuple[float, list[float] | None]] = {}
_vec_lock = threading.Lock()


def query_vector(query: str) -> list[float] | None:
    """The embedding for this query text — computed once, then reused for the
    short window in which a page and its count are fetched. None means the
    embedder had nothing to say, and every caller in that window agrees."""
    now = time.monotonic()
    with _vec_lock:
        hit = _vec_cache.get(query)
        if hit and now - hit[0] < _VEC_TTL_SECONDS:
            return hit[1]
    vec = llm.embed(query)
    with _vec_lock:
        if len(_vec_cache) >= _VEC_CACHE_MAX:
            for stale in [k for k, (at, _) in _vec_cache.items()
                          if now - at >= _VEC_TTL_SECONDS] or [next(iter(_vec_cache))]:
                _vec_cache.pop(stale, None)
        _vec_cache[query] = (now, vec)
    return vec


def _hybrid_args(query: str, k: int = 10, offset: int = 0) -> dict:
    vec = query_vector(query)
    return {
        "q": query, "vec": str(vec) if vec else "[" + ",".join(["0"] * 768) + "]",
        "has_vec": vec is not None, "like": like_pattern(query),
        "floor": SIM_FLOOR, "ann": ANN_CANDIDATES,
        "k": max(1, min(int(k), MAX_K)), "offset": max(0, int(offset)),
    }


def hybrid_search(query: str, k: int = 10, offset: int = 0) -> list[dict]:
    rows = _rank_hybrid(query)
    start = max(0, int(offset))
    stop = start + max(1, min(int(k), MAX_K))
    return rows[start:stop]


def hybrid_count(query: str) -> int:
    """How many documents this query matches, corpus-wide — not how many were
    returned. The console says "showing N of M" and M has to be the corpus's
    answer, or the sentence is a claim nobody can trace."""
    return len(_rank_hybrid(query))


# MUVERA/PolarQuant replaces the pgvector ANN half of the old CTE above. The
# keyword half remains canonical-content SQL during the Iceberg migration;
# both result lists are fused and cached together so `search` and
# `searchTotal` still describe exactly the same approximate candidate set.
_RANK_TTL_SECONDS = 120.0
_rank_cache: dict[str, tuple[float, list[dict]]] = {}
_rank_lock = threading.Lock()


def _keyword_score(row: dict, terms: list[str]) -> float:
    if not terms:
        return 1.0
    title = str(row.get("title") or "").lower()
    text = f"{row.get('snippet') or ''} {row.get('body') or ''}".lower()
    hits = sum(2 * title.count(term) + min(3, text.count(term)) for term in terms)
    return min(1.0, hits / max(1.0, len(terms) * 2.0))


def _rank_hybrid(query: str) -> list[dict]:
    now = time.monotonic()
    with _rank_lock:
        hit = _rank_cache.get(query)
        if hit and now - hit[0] < _RANK_TTL_SECONDS:
            return hit[1]

    pattern = like_pattern(query.strip())
    if query.strip():
        keyword_rows = q("""
          SELECT d.id, d.source, d.title, d.snippet, d.body, d.author, d.author_initials,
                 d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags,
                 coalesce(max(td.search_weight), 1.0) AS boost
            FROM documents d
            LEFT JOIN tags t ON t.document_id = d.id
            LEFT JOIN tag_definitions td ON td.tag = t.tag
           WHERE d.title ILIKE %s OR d.snippet ILIKE %s OR d.body ILIKE %s
           GROUP BY d.id
           ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
           LIMIT %s""", (pattern, pattern, pattern, MAX_K * 2))
    else:
        keyword_rows = q("""
          SELECT d.id, d.source, d.title, d.snippet, d.body, d.author, d.author_initials,
                 d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags,
                 coalesce(max(td.search_weight), 1.0) AS boost
            FROM documents d
            LEFT JOIN tags t ON t.document_id = d.id
            LEFT JOIN tag_definitions td ON td.tag = t.tag
           GROUP BY d.id
           ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
           LIMIT %s""", (MAX_K * 2,))

    semantic: dict[int, float] = {}
    vec = query_vector(query) if query.strip() else None
    if vec is not None:
        try:
            if retrieval.ensure_index():
                semantic = {h["document_id"]: h["score"] for h in retrieval.INDEX.search(
                    np.asarray([vec], np.float32), k=ANN_CANDIDATES, candidate_k=1000)}
        except (OSError, ValueError):
            semantic = {}

    keyword_ids = {int(row["id"]) for row in keyword_rows}
    rows_by_id = {int(row["id"]): row for row in keyword_rows}
    missing = [doc_id for doc_id, score in semantic.items()
               if score > SIM_FLOOR and doc_id not in rows_by_id]
    if missing:
        for row in q("""
          SELECT d.id, d.source, d.title, d.snippet, d.body, d.author, d.author_initials,
                 d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags,
                 coalesce(max(td.search_weight), 1.0) AS boost
            FROM documents d
            LEFT JOIN tags t ON t.document_id = d.id
            LEFT JOIN tag_definitions td ON td.tag = t.tag
           WHERE d.id = ANY(%s) GROUP BY d.id""", (missing,)):
            rows_by_id[int(row["id"])] = row

    terms = [word for word in re.findall(r"[a-z0-9][a-z0-9_-]*", query.lower()) if len(word) > 1]
    ranked = []
    for doc_id, row in rows_by_id.items():
        kw = _keyword_score(row, terms)
        sim = semantic.get(doc_id, 0.0)
        # Keyword candidates remain eligible even when the tokenizer produced
        # no useful term; semantic-only candidates must clear the shared floor.
        if doc_id not in keyword_ids and sim <= SIM_FLOOR:
            continue
        boost = float(row.get("boost", 1.0) or 1.0)
        row.pop("boost", None)
        row["score"] = (kw * 2.0 + max(sim - SIM_FLOOR, 0.0) * 3.0) * boost
        ranked.append(row)
    ranked.sort(key=lambda row: (
        -float(row["score"]),
        -(row.get("updated_src") or dt.date.min).toordinal(),
        -int(row["id"]),
    ))
    with _rank_lock:
        if len(_rank_cache) >= _VEC_CACHE_MAX:
            _rank_cache.pop(next(iter(_rank_cache)), None)
        _rank_cache[query] = (now, ranked)
    return ranked


# ————————————————— search telemetry (SRCH-4) —————————————————
#
# The console pages by re-issuing the same query with a larger k, so logging on
# every call recorded one row per "show more" and `insightStats.searches` grew
# with scrolling rather than with searching. A query is logged once per window
# per person: the second page of a search is the same search.
#
# The dedupe is a NOT EXISTS on the insert, not a read-then-write, so two
# concurrent pages of the same query cannot both find nothing and both insert.
SEARCH_LOG_WINDOW = "5 minutes"


def log_search_once(query: str) -> None:
    """Record that someone searched for `query` — unless the same person
    already searched for it inside the window, in which case this is a later
    page of that search and there is nothing new to record."""
    detail = query[:120]
    try:
        exec_(f"""
          INSERT INTO usage_log (kind, detail)
          SELECT 'search', %s
           WHERE NOT EXISTS (SELECT 1 FROM usage_log
                              WHERE kind = 'search' AND detail = %s
                                AND at > now() - interval '{SEARCH_LOG_WINDOW}')""",
              (detail, detail))
    except Exception:  # noqa: BLE001 — telemetry never breaks the search it rides on
        pass


# ————————————————— fact contradiction detection —————————————————
# Deterministic, no model call. Two stored claims contradict when they are
# about the same thing — their non-numeric, non-negating words overlap almost
# completely — but they disagree on either the numbers they state or on
# polarity (one negates, the other does not). Both sides of every pair are
# real `facts` rows: the detector pairs claims, it never writes one, and a
# ledger with no such pair yields an empty list rather than a warning.
#
# The overlap bar is deliberately high. A missed contradiction shows up again
# the next time someone reads the two claims; a false one trains the team to
# dismiss the banner.

_NEGATIONS = {"not", "no", "never", "cannot", "can't", "cant", "won't", "wont",
              "doesn't", "doesnt", "don't", "dont", "isn't", "isnt", "aren't",
              "arent", "without", "disabled", "unsupported", "unavailable"}
_STOP = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
         "in", "on", "for", "and", "or", "it", "its", "this", "that", "these",
         "we", "our", "us", "by", "at", "as", "with", "from", "has", "have"}
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'’\-]*")

# A pair must share at least this many topic words, and this fraction of their
# combined vocabulary, before a numeric or polarity difference counts.
_MIN_SHARED = 3
_MIN_OVERLAP = 0.8


def _claim_shape(claim: str) -> tuple[frozenset[str], tuple[str, ...], bool]:
    """(topic words, the numbers stated, whether the claim negates)."""
    text = claim.lower().replace("’", "'")
    numbers = tuple(sorted(n.replace(",", "") for n in _NUM_RE.findall(text)))
    words = _WORD_RE.findall(_NUM_RE.sub(" ", text))
    negated = any(w in _NEGATIONS for w in words)
    topic = frozenset(w for w in words if w not in _STOP and w not in _NEGATIONS)
    return topic, numbers, negated


def detect_contradictions(rows: list[dict]) -> list[tuple[dict, dict, str, str]]:
    """(fact, other fact, reason, detail) for every contradicting pair."""
    shaped = [(r, *_claim_shape(r["claim"])) for r in rows]
    out: list[tuple[dict, dict, str, str]] = []
    for i, (a, topic_a, nums_a, neg_a) in enumerate(shaped):
        for b, topic_b, nums_b, neg_b in shaped[i + 1:]:
            shared, union = topic_a & topic_b, topic_a | topic_b
            if len(shared) < _MIN_SHARED or not union:
                continue
            if len(shared) / len(union) < _MIN_OVERLAP:
                continue
            if nums_a != nums_b:
                reason = "numeric"
                detail = f"{' / '.join(nums_a) or 'no figure'} vs {' / '.join(nums_b) or 'no figure'}"
            elif neg_a != neg_b:
                reason = "polarity"
                detail = "one claim negates what the other asserts"
            else:
                continue
            out.append((a, b, reason, detail))
    return out


# ————————————————— secret masking —————————————————
# Settings rows and MCP tokens carry credentials; the GraphQL read path never
# returns them verbatim. Masked shape: "••••…last4" plus a *_set boolean so the
# Bots setup UI can still show "configured" without the secret.

_SECRET_SETTING_FIELDS = {
    "slack_bot": ("bot_token", "signing_secret"),
    "github_bot": ("webhook_secret",),
}


def _mask_secret(v) -> str:
    if not isinstance(v, str) or not v:
        return ""
    return "••••…" + v[-4:] if len(v) > 8 else "••••"


def _details(row: dict) -> list[AuditDetail]:
    """events.detail (a jsonb array of {label,value}) as GraphQL rows. Rows
    written before the column existed decode to [], which is honest: nothing
    beyond actor/verb/target/at was recorded for them."""
    out = []
    for d in jload(row.get("detail")) or []:
        if isinstance(d, dict) and d.get("label"):
            out.append(AuditDetail(label=str(d["label"]), value=str(d.get("value", ""))))
    return out


def _audit_where(query: str, date_from: str | None, date_to: str | None) -> tuple[str, tuple]:
    """The access log's filter, as one predicate. Shared by `audit_log` and
    `audit_log_total` so the count and the rows can never describe different
    filters."""
    clauses, args = [], []
    text = (query or "").strip()
    if text:
        clauses.append("(actor ILIKE %s OR verb ILIKE %s OR target ILIKE %s)")
        args += [f"%{text}%"] * 3
    floor, ceil = _iso_date_arg(date_from), _iso_date_arg(date_to)
    if floor:
        clauses.append("occurred_at >= %s")
        args.append(floor)
    if ceil:
        # Inclusive of the whole end day: the filter names dates, not instants.
        clauses.append("occurred_at < (%s::date + 1)")
        args.append(ceil)
    return (" AND ".join(clauses) if clauses else "true"), tuple(args)


def _mask_setting(key: str, value):
    if key == "setup_token":
        return _mask_secret(value if isinstance(value, str) else json.dumps(value))
    if key == "llm" and isinstance(value, dict) and isinstance(value.get("keys"), dict):
        # Provider API keys live one level down and were coming back verbatim.
        # The console only ever shows whether a key is set and its last four,
        # so that is all this returns.
        value = dict(value)
        value["keys"] = {k: _mask_secret(v) for k, v in value["keys"].items()}
    fields = _SECRET_SETTING_FIELDS.get(key)
    if not fields or not isinstance(value, dict):
        return value
    out = dict(value)
    for f in fields:
        secret = out.pop(f, None)
        out[f"{f}_set"] = bool(secret)
        if secret:
            out[f"{f}_hint"] = _mask_secret(secret)
    return out


# ————————————————— Query —————————————————


@strawberry.type
class Query:
    @strawberry.field
    def overview_stats(self, since: str | None = None) -> JSON:
        """`since` is an ISO date: the lower bound of the window the dashboard's
        range picker is on. It bounds `changes` — rows in the `changes` table,
        which is what a tile labelled "changes" has to be counting. It used to
        count `events`, the access log, so the number moved when somebody signed
        in and stood still when the drift checker proposed a hundred edits
        (STATS-3).

        The bound is `>=`, closed, the same shape `insightStats` uses. It was
        `>` on the default window and `>=` on the picked one, so the same
        dashboard counted its two windows by two different rules.

        The other three are gauges of the workspace right now (facts awaiting
        review, runs in flight, active flows) and have no window to count over."""
        floor = _iso_date_arg(since)
        if floor:
            changes = q1("SELECT count(*) AS n FROM changes WHERE created_at >= %s", (floor,))["n"]
        else:
            changes = q1("SELECT count(*) AS n FROM changes WHERE created_at >= now() - interval '7 days'")["n"]
        facts_review = q1("SELECT count(*) AS n FROM facts WHERE status <> 'Verified'")["n"]
        running = q1("SELECT count(*) AS n FROM workflow_runs WHERE status IN ('running','waiting')")["n"]
        flows = q1("SELECT count(*) AS n FROM workflows WHERE status = 'active'")["n"]
        return {"changes": int(changes), "factsReview": int(facts_review),
                "flowsRunning": int(running), "flowsActive": int(flows)}

    @strawberry.field
    def source_pulse(self) -> list[SourcePulse]:
        # bars = real per-source doc-change counts by day (last 12 days, empty
        # days = 0), from documents timestamps; [] when a source has no recent
        # activity — never an invented curve.
        daily = q("""SELECT source_id, updated_src AS day, count(*) AS n FROM documents
                     WHERE source_id IS NOT NULL AND updated_src >= current_date - 11
                     GROUP BY source_id, updated_src""")
        per_src: dict[int, dict] = {}
        for d in daily:
            per_src.setdefault(d["source_id"], {})[d["day"]] = int(d["n"])
        today = dt.date.today()

        def bars(source_id: int) -> list[int]:
            days = per_src.get(source_id)
            if not days:
                return []
            return [days.get(today - dt.timedelta(days=11 - i), 0) for i in range(12)]

        def safe_config(r: dict) -> dict:
            """Never leak connector credentials: secret field values are masked
            and bulky internal hash maps dropped (CONNECTORS-CONTRACT.md)."""
            import connect_sync
            cfg = jload(r["config"]) or {}
            if r.get("kind") == "connector":
                return connect_sync.masked_config(r["provider"], cfg)
            if r.get("kind") == "github":
                token = str(cfg.pop("token", "") or "")
                if token:
                    cfg["token_set"] = True
                    cfg["token_hint"] = _mask_secret(token)
            return {k: v for k, v in cfg.items() if k != "shas"}

        # A source's automatic cadence is not a column on `sources`: it is the
        # trigger of the "Sync <name>" flow flowengine.ensure_sync_flow creates
        # for it, keyed by the sync_source step's source_id. Read it back the
        # same way, so the Sources card and the Flows editor can only ever show
        # one cadence for one source.
        schedules: dict[int, tuple[int, int | None]] = {}
        for w in q("SELECT id, status, nodes, trigger FROM workflows"):
            trig = jload(w["trigger"]) or {}
            every = trig.get("every_minutes") if trig.get("on") == "schedule" else None
            for step in jload(w["nodes"]) or []:
                if not isinstance(step, dict) or step.get("kind") != "sync_source":
                    continue
                sid = int((step.get("config") or {}).get("source_id") or 0)
                if sid:
                    # Paused flow = no automatic sync, which is what None says.
                    active = w["status"] == "active" and isinstance(every, int)
                    schedules[sid] = (w["id"], int(every) if active else None)

        return [
            SourcePulse(id=r["id"], provider=r["provider"], name=r["display_name"], status=r["status"],
                        stat=r["stat_num"], unit=r["stat_unit"], bars=bars(r["id"]),
                        docs_count=r["docs_count"], health=r["health"], config=safe_config(r),
                        kind=r.get("kind") or "",
                        last_sync_at=r["last_sync_at"].isoformat() if r.get("last_sync_at") else "",
                        sync_flow_id=schedules.get(r["id"], (None, None))[0],
                        sync_interval_minutes=schedules.get(r["id"], (None, None))[1])
            for r in q("SELECT * FROM sources ORDER BY id")
        ]

    @strawberry.field
    def github_repos(self) -> list[GithubRepo]:
        """Repos visible to the configured token; [] (never errors) without a token."""
        source = q1(
            """SELECT config FROM sources
               WHERE kind = 'github' AND config->>'token' <> ''
               ORDER BY id DESC LIMIT 1"""
        )
        source_cfg = jload(source["config"]) if source else {}
        available_token = github.token() or str((source_cfg or {}).get("token") or "")
        if not available_token:
            return []
        token_state = github.push_token(available_token)
        try:
            repos = github.list_repos()
        except github.GithubError:
            return []
        finally:
            github.pop_token(token_state)
        connected = {jload(r["config"]).get("repo", "")
                     for r in q("SELECT config FROM sources WHERE kind = 'github'")}
        return [GithubRepo(
            full_name=r["full_name"], description=r.get("description") or "",
            private=bool(r.get("private")), default_branch=r.get("default_branch") or "main",
            updated_at=r.get("updated_at") or "", connected=r["full_name"] in connected,
        ) for r in repos]

    @strawberry.field
    def sync_status(self, source_id: int) -> SyncStatus:
        live = ingest.status(source_id)
        src = q1("SELECT kind, config, last_sync_at FROM sources WHERE id = %s", (source_id,))
        cfg = jload(src["config"]) if src else {}
        # github cursors are commit shas (shown short); connector cursors are
        # provider-native (timestamps, delta tokens) and shown whole.
        cursor = cfg.get("cursor") or ""
        if src and src.get("kind") == "github":
            cursor = cursor[:7]
        counts = q1("""
          SELECT count(DISTINCT d.id) AS docs, count(c.id) AS chunks,
                 count(c.id) FILTER (WHERE c.embedding IS NOT NULL) AS embedded
          FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
          WHERE d.source_id = %s""", (source_id,)) or {"docs": 0, "chunks": 0, "embedded": 0}
        last_sync = src["last_sync_at"].isoformat() if src and src["last_sync_at"] else (cfg.get("last_sync_at") or "")
        return SyncStatus(
            state=live["state"], phase=live["phase"], done=live["done"], total=live["total"],
            last_sync_at=last_sync,
            last_error=live["error"] or cfg.get("last_error", ""),
            cursor=cursor[:40],
            doc_count=int(counts["docs"]), chunk_count=int(counts["chunks"]),
            embedded_count=int(counts["embedded"]))

    @strawberry.field
    def search(self, query: str = "", k: int = 10, offset: int = 0) -> list[Document]:
        """One page of results. `offset` is a real SQL offset, so a browser can
        walk a corpus larger than one page instead of the caller pretending the
        first k rows are all there is (see search_total).

        `k` is clamped to MAX_K: a page is a page, and a request for a million
        documents is a request for the whole corpus with the bodies attached."""
        offset, k = max(0, offset), max(1, min(k, MAX_K))
        if query.strip():
            log_search_once(query.strip())
            rows = hybrid_search(query, k, offset)
        else:
            # No query: most-recently-updated, paged the same way.
            rows = q(DOC_SQL.format(where="") + " LIMIT %s OFFSET %s", (k, offset))
        return [_doc(r) for r in rows]

    @strawberry.field
    def search_total(self, query: str = "") -> int:
        """How many documents `search` would return for this query with no
        limit. The count the results feed puts above the list."""
        if query.strip():
            return hybrid_count(query)
        return int(q1("SELECT count(*) AS n FROM documents")["n"])

    @strawberry.field
    def recent_searches(self, limit: int = 6) -> list[str]:
        """The queries this workspace has actually run, newest distinct first.
        Read from usage_log, which the search resolver already writes — nothing
        is recorded here that a person did not type."""
        return [r["detail"] for r in q(
            """SELECT detail, max(at) AS last FROM usage_log
               WHERE kind = 'search' AND detail <> ''
               GROUP BY detail ORDER BY last DESC LIMIT %s""", (max(1, limit),))]

    @strawberry.field
    def related_documents(self, document_id: int) -> list[RelatedDoc]:
        """Documents one lineage edge away, in both directions. Real `edges`
        rows only: a document nothing links to answers [], which is the truth
        about it rather than an empty section with no explanation."""
        # The inbound half (`e.to_doc = %s`) had no index to sit on until
        # edges_to_doc_idx (STATS-5) — the unique index leads with from_doc, so
        # this read the whole edge table to answer "what links here". Ordering
        # ends in id so two documents with the same title do not swap places
        # between reads.
        rows = q("""
          SELECT d.id, d.source, d.title, e.rel, 'out' AS direction
          FROM edges e JOIN documents d ON d.id = e.to_doc WHERE e.from_doc = %s
          UNION ALL
          SELECT d.id, d.source, d.title, e.rel, 'in' AS direction
          FROM edges e JOIN documents d ON d.id = e.from_doc WHERE e.to_doc = %s
          ORDER BY title, id, rel""", (document_id, document_id))
        return [RelatedDoc(id=r["id"], source=r["source"], title=r["title"],
                           rel=r["rel"], direction=r["direction"]) for r in rows]

    @strawberry.field
    def document(self, id: int) -> Document | None:
        rows = q(DOC_SQL.format(where="WHERE d.id = %s"), (id,))
        if not rows:
            return None
        # Per-user, and it must be the same name toggleWatch writes, or the
        # star never comes back lit for the person who set it.
        watched = q1("SELECT 1 AS x FROM watches WHERE user_name = %s AND document_id = %s",
                     (actor_name(), id)) is not None
        return _doc(rows[0], watched)

    @strawberry.field
    def revisions(self, document_id: int) -> list[AuditEvent]:
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in q("""SELECT e.* FROM events e JOIN documents d ON e.target = d.title
                              WHERE d.id = %s ORDER BY e.occurred_at DESC LIMIT 20""", (document_id,))]

    @strawberry.field
    def findings(self, document_id: int) -> list[Finding]:
        return [Finding(id=r["id"], kind=r["kind"], severity=r["severity"], text=r["text"], note=r["note"])
                for r in q("SELECT * FROM findings WHERE document_id = %s ORDER BY id", (document_id,))]

    @strawberry.field
    def changes(self, document_id: int) -> list[Change]:
        return [Change(id=r["id"], original=r["original"], replacement=r["replacement"],
                       reason=r["reason"], status=r["status"])
                for r in q("SELECT * FROM changes WHERE document_id = %s ORDER BY id", (document_id,))]

    @strawberry.field
    def lineage(self) -> list[LineageNode]:
        # The two degree counts used to be correlated subqueries evaluated once
        # per document — with no index on edges(to_doc), that is O(documents ×
        # edges) and it grew quadratically with the graph the page exists to
        # draw (STATS-5). `degree` aggregates the edge table ONCE, in a single
        # pass over both endpoints, and the result is joined in. Documents with
        # no edges are absent from `degree` and coalesce to 0, which is the same
        # answer count(*) gave them.
        rows = q("""
          WITH degree AS (
            SELECT doc, sum(inb) AS inbound, sum(outb) AS outbound FROM (
              SELECT to_doc   AS doc, 1 AS inb, 0 AS outb FROM edges
              UNION ALL
              SELECT from_doc AS doc, 0 AS inb, 1 AS outb FROM edges
            ) x GROUP BY doc
          )
          SELECT d.id, d.source, d.external_id, d.title, d.author, d.updated_src, d.created_src,
                 d.graph_x, d.graph_y, d.graph_icon, d.graph_meta,
                 d.kind, d.source_path, d.source_id, s.kind AS src_kind,
                 s.config->>'repo' AS repo,
                 array_remove(array_agg(DISTINCT t.tag), NULL) AS tags,
                 coalesce(max(g.inbound), 0)::int AS inbound,
                 coalesce(max(g.outbound), 0)::int AS outbound
          FROM documents d
          LEFT JOIN tags t ON t.document_id = d.id
          LEFT JOIN sources s ON s.id = d.source_id
          LEFT JOIN degree g ON g.doc = d.id
          GROUP BY d.id, s.kind, s.config ORDER BY d.id""")
        pos = layout_nodes(rows)
        today = dt.date.today()
        out = []
        for r in rows:
            x, y = pos[r["id"]]
            doc_kind, group = classify_node(r)
            tags = r["tags"] or []
            updated, created = r["updated_src"], r["created_src"] or r["updated_src"]
            out.append(LineageNode(
                id=r["external_id"], doc_id=r["id"], source=r["source"], title=r["title"],
                meta=r["graph_meta"] or f"{r['author']} · {updated.strftime('%b %-d, %Y') if updated else '—'}",
                icon=r["graph_icon"] or SOURCE_ICONS.get(r["source"], "doc"),
                x=round(x, 4), y=round(y, 4), pinned=r["graph_x"] is not None,
                date=updated.isoformat() if updated else "",
                created_date=created.isoformat() if created else "",
                warn=bool({"stale", "needs-review"} & set(tags)),
                owner=r["author"], tags=tags,
                stale_days=(today - updated).days if updated else 0,
                orphan=r["inbound"] + r["outbound"] == 0,
                inbound=r["inbound"], outbound=r["outbound"],
                doc_kind=doc_kind, group=group))
        return out

    @strawberry.field
    def lineage_edges(self) -> list[LineageEdge]:
        rows = q("""
          SELECT e.id, f.external_id AS from_id, t.external_id AS to_id, e.rel, e.created_at,
                 e.curve, e.meta
          FROM edges e JOIN documents f ON f.id = e.from_doc JOIN documents t ON t.id = e.to_doc
          ORDER BY e.id""")
        return [LineageEdge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], kind=r["rel"],
                            date=r["created_at"].isoformat() if r["created_at"] else "",
                            curve=r["curve"], meta=jload(r["meta"])) for r in rows]

    @strawberry.field
    def graph_stats(self) -> GraphStats:
        # "stale" is relative to the newest doc so seeded 2024 data reads sensibly.
        s = q1("""
          SELECT count(*) AS docs,
                 count(*) FILTER (WHERE d.updated_src < (SELECT max(updated_src) FROM documents) - 45
                                  OR EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id
                                             AND t.tag IN ('stale','needs-review'))) AS stale,
                 count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM edges e WHERE e.from_doc = d.id OR e.to_doc = d.id)) AS orphans,
                 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id
                                                AND t.tag = 'customer-facing')
                                  AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.rel = 'translates'
                                                  AND (e.from_doc = d.id OR e.to_doc = d.id))) AS untranslated,
                 count(*) FILTER (WHERE d.author = '' OR d.author = 'CI') AS unowned
          FROM documents d""")
        contradictions = q1("SELECT count(*) AS n FROM edges WHERE rel = 'contradicts'")["n"]
        cited = q("""SELECT d.title, d.id, count(*) AS inbound FROM edges e
                     JOIN documents d ON d.id = e.to_doc
                     GROUP BY d.id ORDER BY inbound DESC, d.id LIMIT 3""")
        activity = q("""SELECT * FROM (
                          SELECT occurred_at::date AS day, count(*) AS n FROM events
                          GROUP BY 1 ORDER BY 1 DESC LIMIT 60) x ORDER BY day""")
        return GraphStats(
            docs=s["docs"], stale=s["stale"], orphans=s["orphans"],
            untranslated=s["untranslated"], unowned=s["unowned"], contradictions=int(contradictions),
            top_cited=[TopCited(title=r["title"], doc_id=r["id"], inbound=r["inbound"]) for r in cited],
            activity=[ActivityBucket(date=r["day"].isoformat(), count=r["n"]) for r in activity])

    @strawberry.field
    def doc_history(self, document_id: int) -> list[DocHistory]:
        return [DocHistory(at=r["at"], actor=r["actor"], verb=r["verb"], detail=r["target"])
                for r in q("""SELECT e.actor, e.verb, e.target,
                                     to_char(e.occurred_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS at
                              FROM events e JOIN documents d ON e.target = d.title
                              WHERE d.id = %s ORDER BY e.occurred_at DESC LIMIT 30""", (document_id,))]

    @strawberry.field
    def graph_views(self) -> list[GraphView]:
        return [GraphView(id=r["id"], name=r["name"], state=json.dumps(jload(r["state"])))
                for r in q("SELECT id, name, state FROM graph_views ORDER BY id")]

    @strawberry.field
    def facts(self) -> list[Fact]:
        # verified is facts.verified_at (a DATE) as ISO — not the legacy
        # facts.verified display string, which the console cannot re-format
        # and whose '—' placeholder parses as an invalid date.
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"],
                     verified=r["verified_at"].isoformat() if r["verified_at"] else "")
                for r in q("SELECT * FROM facts ORDER BY id")]

    @strawberry.field
    def claims(self, document_id: int) -> list[Fact]:
        """The claims mined from ONE document, by key. `facts.document_id` is
        written by the extractor at the moment it reads a document, so this is
        provenance the server recorded, never provenance inferred from the
        free-text `facts.source` label. Facts landed before the column existed
        (and hand-written ones with no citation) carry NULL and belong to no
        document — they are absent here rather than attributed to a guess."""
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"],
                     verified=r["verified_at"].isoformat() if r["verified_at"] else "")
                for r in q("SELECT * FROM facts WHERE document_id = %s ORDER BY id", (document_id,))]

    @strawberry.field
    def fact_contradictions(self) -> list[FactContradiction]:
        """Claims in the ledger that disagree with each other. Deterministic
        (see detect_contradictions) — no model call, and [] on a ledger whose
        claims are consistent, which is the common case."""
        rows = q("SELECT id, claim, status FROM facts ORDER BY id")
        return [FactContradiction(
            fact_id=a["id"], claim=a["claim"], status=a["status"],
            other_fact_id=b["id"], other_claim=b["claim"], other_status=b["status"],
            reason=reason, detail=detail)
            for a, b, reason, detail in detect_contradictions(rows)]

    @strawberry.field
    def tasks(self) -> list[Task]:
        # overdue is derived in SQL against the database's own current_date, so
        # it cannot drift from the due date the same row reports.
        rows = q("""SELECT *, (due_date IS NOT NULL AND NOT done AND due_date < current_date) AS overdue
                    FROM tasks ORDER BY id""")
        return [Task(id=r["id"], title=r["title"], assignee_initials=r["assignee_initials"],
                     assignee_tint=r["assignee_tint"], kind=r["kind"], kind_label=r["kind_label"],
                     done=r["done"], due=r["due_date"].isoformat() if r["due_date"] else "",
                     overdue=bool(r["overdue"]))
                for r in rows]

    @strawberry.field
    def tasks_summary(self) -> TaskSummary | None:
        """The strip above the board: counts and rosters rolled up off the same
        rows `tasks` returns. None on an empty inbox — there is nothing to
        summarise, and the board renders without a headline."""
        s = q1("""SELECT count(*) AS total,
                         count(*) FILTER (WHERE NOT done) AS open,
                         count(*) FILTER (WHERE done) AS done,
                         count(*) FILTER (WHERE NOT done AND due_date < current_date) AS overdue,
                         count(*) FILTER (WHERE NOT done AND due_date >= current_date
                                          AND due_date <= current_date + 7) AS due_soon
                  FROM tasks""")
        if not s or not s["total"]:
            return None
        tags = [r["kind_label"] for r in q(
            "SELECT DISTINCT kind_label FROM tasks WHERE kind_label <> '' ORDER BY kind_label")]
        people = [r["assignee_initials"] for r in q(
            """SELECT DISTINCT assignee_initials FROM tasks
               WHERE NOT done AND assignee_initials <> '' ORDER BY assignee_initials""")]
        # The headline is whichever number needs acting on: a missed deadline
        # outranks the open count, and "all caught up" is itself the news.
        if s["overdue"]:
            value, label = str(s["overdue"]), "overdue"
        elif s["open"]:
            value, label = str(s["open"]), "open"
        else:
            value, label = str(s["done"]), "done"
        return TaskSummary(
            title="Review queue", tags=tags, people=people,
            stat_value=value, stat_label=label,
            open_count=int(s["open"]), done_count=int(s["done"]),
            overdue_count=int(s["overdue"]), due_soon_count=int(s["due_soon"]))

    # `askMari` used to live here, reading the last row of `ask_answers`. That
    # table has no writer anywhere in the tree, so the field raised IndexError
    # on every install, and nothing in the console queried it: the live
    # question-and-answer surface is `approvedAnswers` (app.py /chat serves it,
    # mutations_knowledge writes it). A field that can only throw is worse than
    # no field, so it is gone. Left for whoever owns those files:
    # gqltypes.AskMari/AskSource and init.sql's `ask_answers` table are now
    # unreferenced and should go with it.

    @strawberry.field
    def digest(self) -> list[DigestTopic]:
        return [DigestTopic(title=r["title"], summary=r["summary"],
                            where=[DigestWhere(**w) for w in jload(r["wheres"])],
                            impact=[DigestImpact(**i) for i in jload(r["impact"])])
                for r in q("SELECT * FROM digest_topics ORDER BY id")]

    @strawberry.field
    def members(self) -> list[Member]:
        return [Member(id=r["id"], name=r["name"], initials=r["initials"], tint=r["tint"],
                       email=r["email"], role=r["role"], provider=r["provider"], status=r["status"],
                       joined=r["joined"].isoformat())
                for r in q("SELECT * FROM users ORDER BY id")]

    @strawberry.field
    def glossary(self) -> list[GlossaryTerm]:
        return [GlossaryTerm(id=r["id"], term=r["term"], definition=r["definition"],
                             owner=r["owner_name"], updated=r["updated"].isoformat())
                for r in q("SELECT * FROM glossary ORDER BY term")]

    @strawberry.field
    def tag_defs(self) -> list[TagDef]:
        # usage is a real count off the tags table — the Library's tag panel
        # reads "used on N of M documents", so a definition nobody has applied
        # must come back as 0 rather than as a blank the UI can round up.
        return [TagDef(tag=r["tag"], label=r["label"], kind=r["kind"], search_weight=r["search_weight"],
                       is_default=r["is_default"], behaviors=r["behaviors"], usage=int(r["usage"]))
                for r in q("""SELECT d.*, (SELECT count(*) FROM tags t WHERE t.tag = d.tag) AS usage
                              FROM tag_definitions d ORDER BY d.search_weight DESC""")]

    # ——— editorial system: style guides, rule registry, templates, voice ———

    @strawberry.field
    def style_guides(self) -> list[StyleGuide]:
        """The style packs this workspace can adopt. `rules` and `preview` are
        read off `style_rules` in one pass, so a pack's advertised rule count
        is literally the rules it has. A workspace that deleted every shipped
        pack gets [] and the guides tab renders its own empty state."""
        rules: dict[str, list[str]] = {}
        for r in q("SELECT guide_key, description FROM style_rules ORDER BY guide_key, sort, id"):
            rules.setdefault(r["guide_key"], []).append(r["description"])
        return [StyleGuide(key=g["key"], name=g["name"], description=g["description"],
                           tone=g["tone"], builtin=g["builtin"],
                           rules=len(rules.get(g["key"], [])), preview=rules.get(g["key"], []))
                for g in q("SELECT * FROM style_guides ORDER BY sort, key")]

    @strawberry.field
    def style_rules(self, guide_key: str | None = None) -> list[StyleRule]:
        """The deterministic rule registry, optionally one pack's. The Library's
        rules tab counts this list, so the number on the tab strip is the number
        of rules a document is actually checked against."""
        where = "WHERE guide_key = %s" if guide_key else ""
        return [StyleRule(id=r["id"], guide_key=r["guide_key"], family=r["family"],
                          severity=r["severity"], description=r["description"],
                          pack=r["pack"], suggestion=r["suggestion"])
                for r in q(f"SELECT * FROM style_rules {where} ORDER BY guide_key, sort, id",
                           (guide_key,) if guide_key else ())]

    @strawberry.field
    def default_style_pack(self) -> str:
        """The pack this workspace adopted (`style_guide.default_pack`), or ''
        when nobody has chosen one — a real state on a fresh install."""
        row = q1("SELECT value FROM settings WHERE key = 'style_guide'")
        v = jload(row["value"]) if row else {}
        return str(v.get("default_pack") or "") if isinstance(v, dict) else ""

    @strawberry.field
    def voice_layer(self) -> VoiceLayer:
        """The workspace's voice layer (`settings.voice`). Blank and all-off on
        a workspace that has not written one down, which the panel renders as
        empty fields rather than as someone else's voice."""
        row = q1("SELECT value FROM settings WHERE key = 'voice'")
        v = jload(row["value"]) if row else {}
        if not isinstance(v, dict):
            v = {}
        return VoiceLayer(voice=str(v.get("voice") or ""), terms=str(v.get("terms") or ""),
                          banned=str(v.get("banned") or ""), inclusive=bool(v.get("inclusive")),
                          jargon=bool(v.get("jargon")), sentence_case=bool(v.get("sentence_case")))

    @strawberry.field
    def document_templates(self) -> list[DocumentTemplate]:
        """Scaffolds a new document can start from. `standard` separates the
        shipped set from templates this workspace wrote."""
        return [DocumentTemplate(key=r["key"], name=r["name"], category=r["category"],
                                 description=r["description"],
                                 sections=[str(s) for s in (jload(r["sections"]) or [])],
                                 icon=r["icon"], standard=r["standard"])
                for r in q("SELECT * FROM document_templates ORDER BY sort, key")]

    @strawberry.field
    def upload_manifest(self) -> UploadManifest:
        """What the Upload connector ingested, per file: chunks written and how
        many carry a vector. Counted off `chunks` at read time. A workspace that
        has uploaded nothing gets an empty manifest and a '' summary — never a
        sample receipt."""
        rows = q("""SELECT d.id, d.source_path, d.external_id, d.updated_src,
                           count(c.id) AS chunks,
                           count(c.embedding) AS embedded
                    FROM documents d
                    JOIN sources s ON s.id = d.source_id AND s.provider = 'upload'
                    LEFT JOIN chunks c ON c.document_id = d.id
                    GROUP BY d.id ORDER BY d.id""")
        files = []
        for r in rows:
            # source_path is 'upload/<file>' and external_id 'upload:<file>';
            # the row shows the file name the person actually dropped in.
            name = (r["source_path"] or "").split("/", 1)[-1] or r["external_id"].split(":", 1)[-1]
            chunks, embedded = int(r["chunks"]), int(r["embedded"])
            files.append(UploadFile(
                name=name, doc_id=r["id"], chunks=chunks, embedded=embedded,
                detail=f"{chunks} chunk{'' if chunks == 1 else 's'} · {embedded} embedded",
                ingested_at=r["updated_src"].isoformat() if r["updated_src"] else ""))
        total_chunks = sum(f.chunks for f in files)
        total_embedded = sum(f.embedded for f in files)
        summary = (f"{len(files)} file{'' if len(files) == 1 else 's'} · "
                   f"{total_chunks} chunks · {total_embedded} embedded") if files else ""
        return UploadManifest(files=files, file_count=len(files), chunk_count=total_chunks,
                              embedded_count=total_embedded, summary=summary)

    # ——— doc-site presentation: theme presets, generator switches ———

    @strawberry.field
    def site_theme_presets(self) -> list[SiteThemePreset]:
        """Themes the generator can render, with the accent each one ships when
        a site has not overridden it. sitebuilder reads the same rows."""
        return [SiteThemePreset(key=r["key"], name=r["name"], accent=r["accent"], bg=r["bg"])
                for r in q("SELECT * FROM site_theme_presets ORDER BY sort, key")]

    @strawberry.field
    def site_features(self, site_id: int | None = None) -> list[SiteFeature]:
        """The generator's switches. Without a site id, `on` is the shipped
        default; with one, the site's stored override wins. Every key here is
        read by sitebuilder — the page never offers a toggle that changes
        nothing about the built site."""
        overrides: dict = {}
        if site_id:
            row = q1("SELECT features FROM sites WHERE id = %s", (site_id,))
            if row:
                overrides = jload(row["features"]) or {}
        return [SiteFeature(key=r["key"], label=r["label"], hint=r["hint"],
                            on=bool(overrides.get(r["key"], r["default_on"])))
                for r in q("SELECT * FROM site_feature_defs ORDER BY sort, key")]

    @strawberry.field
    def api_keys(self) -> list[ApiKey]:
        return [ApiKey(id=r["id"], name=r["name"], prefix=r["prefix"], scopes=r["scopes"],
                       created=r["created_at"].isoformat(), last_used=r["last_used"], revoked=r["revoked"])
                for r in q("SELECT * FROM api_keys ORDER BY id")]

    @strawberry.field
    def mcp_servers(self) -> list[McpServer]:
        # token is shown once at creation (createMcpServer) — never re-served here.
        return [McpServer(id=r["id"], name=r["name"], url=r["url"], scope=r["scope"],
                          status=r["status"], tools=r["tools"], config=jload(r["config"]),
                          token=_mask_secret(r["token"]))
                for r in q("SELECT * FROM mcp_servers ORDER BY id")]

    @strawberry.field
    def checkpoints(self) -> list[Checkpoint]:
        return [Checkpoint(id=r["id"], provider=r["provider"], item=r["item"], stage=r["stage"],
                           progress=r["progress"], total=r["total"], cursor_id=r["cursor_id"],
                           duration=r["duration"], status=r["status"])
                for r in q("SELECT * FROM ingest_checkpoints ORDER BY id")]

    @strawberry.field
    def sync_events(self) -> list[SyncEvent]:
        return [SyncEvent(id=r["id"], provider=r["provider"], event=r["event"], detail=r["detail"], at=r["at_label"])
                for r in q("SELECT * FROM sync_events ORDER BY id DESC LIMIT 12")]

    @strawberry.field
    def audit_log(self, limit: int = 40, query: str = "",
                  date_from: str | None = None, date_to: str | None = None) -> list[AuditEvent]:
        """`query` matches actor, verb or target; `dateFrom`/`dateTo` are ISO
        dates bounding `occurred_at` inclusively. The access log is deeper than
        any window the console can hold, so the filter has to reach the whole
        table rather than narrowing the page already fetched."""
        where, args = _audit_where(query, date_from, date_to)
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in q(f"SELECT * FROM events WHERE {where} ORDER BY occurred_at DESC, id DESC LIMIT %s",
                           args + (limit,))]

    @strawberry.field
    def audit_log_total(self, query: str = "",
                        date_from: str | None = None, date_to: str | None = None) -> int:
        """Rows matching the same filter, so the console can say what its window
        is a window onto instead of comparing a filtered view against the
        window's own size. No filter = the whole log."""
        where, args = _audit_where(query, date_from, date_to)
        return int(q1(f"SELECT count(*) AS n FROM events WHERE {where}", args)["n"])

    @strawberry.field
    def activity_feed(self, limit: int = 12) -> list[ActivityItem]:
        """Live feed for the overview: runs, edits, deploys — not chatter."""
        rows = q("""
          SELECT id, actor, verb, target,
                 to_char(occurred_at, 'HH24:MI') AS at,
                 -- Age in seconds. The console's live feed counts up from this
                 -- between polls ("42s ago" → "1m ago"), which a wall-clock
                 -- "HH:MI" cannot support: it has no date, so an event from
                 -- yesterday renders as if it just happened.
                 greatest(0, extract(epoch FROM now() - occurred_at))::int AS seconds_ago,
                 CASE
                   WHEN verb LIKE '%%run%%' OR verb LIKE 'started%%' THEN 'run'
                   WHEN verb LIKE 'deploy%%' OR verb LIKE 'rolled%%' THEN 'deploy'
                   WHEN verb LIKE '%%fact%%' OR verb LIKE '%%verif%%' THEN 'fact'
                   WHEN verb LIKE '%%task%%' OR verb IN ('completed','reopened') THEN 'task'
                   WHEN verb LIKE '%%sync%%' OR verb LIKE '%%connect%%' THEN 'sync'
                   WHEN verb LIKE '%%link%%' OR verb LIKE 'derived%%' THEN 'link'
                   ELSE 'edit'
                 END AS kind
          FROM events ORDER BY occurred_at DESC, id DESC LIMIT %s
        """, (limit,))
        return [ActivityItem(id=r["id"], kind=r["kind"], actor=r["actor"],
                             text=r["verb"], target=r["target"], at=r["at"],
                             seconds_ago=int(r["seconds_ago"])) for r in rows]

    @strawberry.field
    def workflows(self) -> list[Workflow]:
        return [Workflow(id=r["id"], name=r["name"], description=r["description"], color=r["color"],
                         pinned=r["pinned"], status=r["status"], nodes=jload(r["nodes"]),
                         trigger=jload(r.get("trigger")) or {})
                for r in q("SELECT * FROM workflows ORDER BY id")]

    @strawberry.field
    def workflow_runs(self, workflow_id: int | None = None) -> list[WorkflowRun]:
        where = "WHERE r.workflow_id = %s" if workflow_id else ""
        rows = q(f"""SELECT r.*, w.name AS wf_name FROM workflow_runs r
                     JOIN workflows w ON w.id = r.workflow_id {where}
                     ORDER BY r.number DESC LIMIT 10""",
                 (workflow_id,) if workflow_id else ())
        return [WorkflowRun(id=r["id"], workflow_id=r["workflow_id"], workflow_name=r["wf_name"],
                            number=r["number"], status=r["status"],
                            # ISO, not started_label: the console formats and
                            # sorts on this (see _doc's date for the same rule).
                            started=r["started_at"].isoformat() if r.get("started_at") else "",
                            duration=r["duration"], progress=r["progress"],
                            stats=jload(r["stats"]), rows=jload(r["rows_data"]),
                            triggered_by=r.get("triggered_by") or "") for r in rows]

    @strawberry.field
    def workflow_run(self, id: int) -> WorkflowRun | None:
        """One run by id. `workflowRuns` only returns the ten newest of a flow,
        which is not a way to follow a specific run someone just started."""
        rows = q("""SELECT r.*, w.name AS wf_name FROM workflow_runs r
                    JOIN workflows w ON w.id = r.workflow_id WHERE r.id = %s""", (id,))
        if not rows:
            return None
        r = rows[0]
        return WorkflowRun(id=r["id"], workflow_id=r["workflow_id"], workflow_name=r["wf_name"],
                           number=r["number"], status=r["status"],
                           started=r["started_at"].isoformat() if r.get("started_at") else "",
                           duration=r["duration"], progress=r["progress"],
                           stats=jload(r["stats"]), rows=jload(r["rows_data"]),
                           triggered_by=r.get("triggered_by") or "")

    @strawberry.field
    def sites(self) -> list[Site]:
        return [Site(id=r["id"], name=r["name"], domain=r["domain"], status=r["status"],
                     theme=jload(r["theme"]), sources=jload(r["sources"]), nav=jload(r["nav"]),
                     gates=jload(r["gates"]), docs=r["docs"], warnings=r["warnings"])
                for r in q("SELECT * FROM sites ORDER BY id")]

    @strawberry.field
    def releases(self, site_id: int | None = None) -> list[Release]:
        """Release history. Omit site_id for every site's, so the Publish page
        can fetch sites and their releases in one document instead of chaining
        a second round trip on the first site's id."""
        where = "WHERE site_id = %s" if site_id else ""
        return [Release(id=r["id"], site_id=r["site_id"], version=r["version"], status=r["status"],
                        deployed=r["deployed"], docs=r["docs"], notes=r["notes"])
                for r in q(f"SELECT * FROM releases {where} ORDER BY site_id, id DESC",
                           (site_id,) if site_id else ())]

    @strawberry.field
    def notifications(self) -> list[Notification]:
        """Only the caller's notifications — markNotificationsRead marks rows
        for `actor_name()`, so reading by anything else leaves the badge stuck."""
        return [Notification(id=r["id"], kind=r["kind"], text=r["text"], detail=r["detail"],
                             at=r["at_label"], read=r["read"])
                for r in q("SELECT * FROM notifications WHERE user_name = %s ORDER BY id",
                           (actor_name(),))]

    @strawberry.field
    def workspace(self) -> Workspace:
        """The `workspace` settings row, which first-run setup writes and
        Settings → Members edits. Fields nobody has filled in come back "" —
        an unnamed workspace is a real state, not a missing fetch."""
        row = q1("SELECT value FROM settings WHERE key = 'workspace'")
        v = jload(row["value"]) if row else {}
        if not isinstance(v, dict):
            v = {}
        return Workspace(name=str(v.get("name") or ""), slug=str(v.get("slug") or ""),
                         plan=str(v.get("plan") or ""), timezone=str(v.get("timezone") or ""),
                         language=str(v.get("language") or ""))

    @strawberry.field
    def provisioning(self) -> Provisioning:
        """Member provisioning as it is actually configured. Manual invites are
        always on (inviteMember is the only path the server implements); the
        GitHub team is whatever an admin saved, and counts as connected only
        when the server also holds a credential to read it with; SCIM has no
        endpoint, so it reports unavailable rather than "Enterprise"."""
        row = q1("SELECT value FROM settings WHERE key = 'provisioning'")
        v = jload(row["value"]) if row else {}
        if not isinstance(v, dict):
            v = {}
        team = str(v.get("github_team") or "")
        has_token = bool(github.token())
        synced = int(q1("SELECT count(*) AS n FROM users WHERE provider = 'github'")["n"])
        providers = [name for name, key in (("github", "github_client_id"),
                                            ("google", "google_client_id"))
                     if config.get("auth", key)]
        return Provisioning(
            manual_invites=bool(v.get("manual_invites", True)),
            github_team=GithubTeamSync(team=team, connected=bool(team and has_token),
                                       credential=has_token, synced_members=synced),
            sso_providers=providers, sso_enabled=bool(providers),
            scim_enabled=bool(v.get("scim_enabled", False)),
            scim_status="unavailable")

    @strawberry.field
    def settings(self) -> list[Setting]:
        return [Setting(key=r["key"], value=_mask_setting(r["key"], jload(r["value"])))
                for r in q("SELECT * FROM settings ORDER BY key")]

    @strawberry.field
    def approved_answers(self) -> list[ApprovedAnswer]:
        return [ApprovedAnswer(id=r["id"], question=r["question"], answer=r["answer"], status=r["status"],
                               owner=r["owner_name"], channels=r["channels"], sources=jload(r["sources"]),
                               served=r["served"], spark=r["spark"],
                               updated=r["updated"].isoformat())
                for r in q("SELECT * FROM approved_answers ORDER BY (status = 'approved') DESC, served DESC")]

    @strawberry.field
    def answer_coverage_gaps(self, limit: int = 8) -> list[str]:
        """Questions people actually asked that no approved answer covers.

        Real demand only: search queries logged in usage_log plus questions put
        to the assistant. A question already served by an approved answer is
        not a gap, so anything matching one verbatim is filtered out. Returns
        [] on a workspace nobody has asked anything in — never a sample list."""
        rows = q("""
          WITH asked AS (
            SELECT lower(trim(detail)) AS question, max(at) AS last_at FROM usage_log
             WHERE kind = 'search' AND length(trim(detail)) >= 8 GROUP BY 1
            UNION ALL
            SELECT lower(trim(content)), max(created_at) FROM chat_messages
             WHERE role = 'user' AND length(trim(content)) BETWEEN 8 AND 200 GROUP BY 1)
          SELECT a.question, max(a.last_at) AS last_at FROM asked a
           WHERE NOT EXISTS (SELECT 1 FROM approved_answers ans
                              WHERE ans.status = 'approved' AND lower(ans.question) = a.question)
           GROUP BY a.question ORDER BY max(a.last_at) DESC LIMIT %s""", (limit,))
        return [r["question"] for r in rows]

    @strawberry.field
    def answer_harvest_sources(self) -> JSON:
        """What `scanAnswerCandidates` would actually have to read, per source
        key it accepts. The wizard used to offer a hardcoded three, so a
        workspace with no Slack was invited to scan Slack and got nothing back.
        Counts, not booleans: the console decides what to offer, and it can
        only offer what there is something to mine."""
        slack = q1("SELECT count(*) AS n FROM documents WHERE source = 'slack'")["n"]
        docs = q1("SELECT count(*) AS n FROM documents WHERE source <> 'slack'")["n"]
        chat = q1("SELECT count(*) AS n FROM chat_messages WHERE role = 'user'")["n"]
        return {"slack": int(slack), "docs": int(docs), "chat": int(chat)}

    @strawberry.field
    def index_stats(self) -> JSON:
        """Corpus size as the embedding config page states it: documents, the
        chunks they were split into, and how many of those carry a vector."""
        s = q1("""SELECT (SELECT count(*) FROM documents) AS docs,
                         count(*) AS chunks,
                         count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded
                  FROM chunks""") or {"docs": 0, "chunks": 0, "embedded": 0}
        return {"docs": int(s["docs"]), "chunks": int(s["chunks"]), "embedded": int(s["embedded"])}

    @strawberry.field
    def bots_status(self) -> JSON:
        """Slack + GitHub bot wiring, as GET /bots/status reports it. Same
        function, so the console's Sources page and the REST surface can never
        disagree about whether a bot is configured."""
        import bots
        return bots.bots_status()

    @strawberry.field
    def connector_catalog(self) -> JSON:
        """The connector catalog, as GET /connectors/catalog reports it: field
        SPECS only, never stored values (CONNECTORS-CONTRACT.md)."""
        import connectors_api
        return connectors_api.catalog()

    @strawberry.field
    def decisions(self) -> list[Decision]:
        rows = q("""SELECT d.*, s.statement AS sup_stmt FROM decisions d
                    LEFT JOIN decisions s ON s.id = d.superseded_by ORDER BY d.id DESC""")
        return [Decision(id=r["id"], statement=r["statement"], context=r["context"], status=r["status"],
                         source_label=r["source_label"], owners=r["owners"],
                         decided_on=r["decided_on"].isoformat() if r["decided_on"] else "",
                         superseded_by=r["superseded_by"], superseded_by_statement=r["sup_stmt"] or "",
                         impact_summary=r["impact_summary"], impact_count=r["impact_count"]) for r in rows]

    @strawberry.field
    def insight_stats(self, since: str | None = None, until: str | None = None) -> InsightStats:
        """Every number is a real count (BOTS-CONTRACT.md §A) — never a constant.

        `since`/`until` are ISO dates bounding the window the dashboard's range
        picker is on. ALL FOUR counts move with the window, so the sentence the
        page writes ("over the last 30 days, from …") describes every tile
        above it and not just the two that happen to have a timestamp."""
        floor, ceil = _iso_date_arg(since), _iso_date_arg(until)
        # One predicate, applied to each table's own timestamp column.
        def window(col: str) -> tuple[str, tuple]:
            clauses, args = [], []
            if floor:
                clauses.append(f"{col} >= %s")
                args.append(floor)
            if ceil:
                clauses.append(f"{col} < (%s::date + 1)")
                args.append(ceil)
            return (" AND ".join(clauses) if clauses else "true"), tuple(args)

        w_usage, a_usage = window("at")
        counts = q1(f"""
          SELECT count(*) FILTER (WHERE kind = 'search') AS searches,
                 count(*) FILTER (WHERE kind = 'chat_answer') AS served,
                 min(at) AS since
          FROM usage_log WHERE {w_usage}""", a_usage) or {"searches": 0, "served": 0, "since": None}
        w_found, a_found = window("created_at")
        drift = q1(f"SELECT count(*) AS n FROM findings WHERE kind IN ('fact','freshness') AND {w_found}",
                   a_found)["n"]
        w_chg, a_chg = window("created_at")
        fixed = q1(f"SELECT count(*) AS n FROM changes WHERE status = 'accepted' AND {w_chg}", a_chg)["n"]
        # The window's own floor when the caller named one; otherwise the first
        # thing this workspace ever did, which is what "since" meant before
        # there was a picker. "" when it has done nothing at all.
        start = floor or (counts["since"].isoformat() if counts["since"] else "")
        return InsightStats(searches=int(counts["searches"]), answers_served=int(counts["served"]),
                            drift_caught=int(drift), docs_fixed=int(fixed), since=start)

    @strawberry.field
    def freshness(self) -> list[FreshnessRow]:
        """Rollup over connected sources only (docs with a source_id) — one row
        per live sources row; seed/sample docs never masquerade as a provider."""
        rows = q("""
          WITH bucketed AS (
            SELECT d.source_id,
                   CASE WHEN d.updated_src IS NULL OR d.updated_src < current_date - 30
                          OR EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id AND t.tag = 'stale')
                        THEN 'stale'
                        WHEN d.updated_src < current_date - 7 THEN 'aging'
                        ELSE 'fresh' END AS bucket
            FROM documents d WHERE d.source_id IS NOT NULL)
          SELECT s.display_name AS source, s.provider AS provider,
                 count(*) FILTER (WHERE b.bucket = 'fresh') AS fresh,
                 count(*) FILTER (WHERE b.bucket = 'aging') AS aging,
                 count(*) FILTER (WHERE b.bucket = 'stale') AS stale
          FROM sources s LEFT JOIN bucketed b ON b.source_id = s.id
          GROUP BY s.id, s.display_name, s.provider ORDER BY count(b.source_id) DESC, s.id""")
        return [FreshnessRow(source=r["source"], provider=r["provider"], fresh=r["fresh"],
                             aging=r["aging"], stale=r["stale"]) for r in rows]

    @strawberry.field
    def readability(self) -> list[ReadabilityRow]:
        rows = q("SELECT id, title, source, readability FROM documents ORDER BY id")
        out = []
        for r in rows:
            grade, note = (r["readability"].split("|", 1) + [""])[:2] if r["readability"] else ("", "")
            out.append(ReadabilityRow(id=r["id"], title=r["title"], source=r["source"], grade=grade, note=note))
        return out

    @strawberry.field
    def glossary_candidates(self) -> list[GlossaryCandidate]:
        # evidence is the document the harvester found the term in — the
        # provenance the review step needs to judge a candidate. '' / 0 for a
        # term typed in by hand, which was never mined from a document.
        return [GlossaryCandidate(id=r["id"], term=r["term"], variants=r["variants"],
                                  definition=r["definition"], evidence=r["evidence"] or "",
                                  evidence_doc_id=r["evidence_doc_id"] or 0)
                for r in q("SELECT * FROM glossary WHERE candidate ORDER BY id")]

    @strawberry.field
    def audit_runs(self) -> list[AuditRun]:
        return [AuditRun(id=r["id"], provider=r["provider"], repo=r["repo"], findings=r["findings"],
                         fixed=r["fixed"], ran_at=r["ran_at"].isoformat())
                for r in q("SELECT * FROM audit_runs ORDER BY id DESC LIMIT 10")]

    @strawberry.field
    def audit_findings(self, run_id: int | None = None) -> list[AuditFinding]:
        where = "WHERE run_id = %s" if run_id else "WHERE run_id = (SELECT max(id) FROM audit_runs)"
        return [AuditFinding(id=r["id"], run_id=r["run_id"], kind=r["kind"], title=r["title"],
                             detail=r["detail"], fix_action=r["fix_action"],
                             fix_payload=jload(r["fix_payload"]), status=r["status"])
                for r in q(f"SELECT * FROM audit_findings {where} ORDER BY kind, id",
                           (run_id,) if run_id else ())]

    @strawberry.field
    def chat_sessions(self) -> list[ChatSession]:
        out = []
        for s in q("SELECT * FROM chat_sessions ORDER BY id DESC LIMIT 20"):
            msgs = q("SELECT * FROM chat_messages WHERE session_id = %s ORDER BY id", (s["id"],))
            out.append(ChatSession(id=s["id"], title=s["title"],
                                   messages=[ChatMessage(id=m["id"], role=m["role"], content=m["content"],
                                                         sources=jload(m["sources"])) for m in msgs]))
        return out

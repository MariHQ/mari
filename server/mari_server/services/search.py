"""Project-scoped hybrid knowledge retrieval."""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
import typing as t

import numpy as np

from mari_server.domain import access
from mari_server.integrations import llm
from mari_server.integrations import vector_index as retrieval
from mari_server.repositories.database import jload
from mari_server.repositories import search as search_store

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


_SEARCH_STOP_WORDS = frozenset({
    "and", "are", "can", "does", "for", "from", "how", "into", "our",
    "the", "their", "this", "was", "what", "when", "where", "which", "who",
    "why", "with", "you", "your",
})


def keyword_patterns(query: str) -> list[str]:
    """Literal LIKE patterns for meaningful words in a natural-language query.

    Requiring the entire question as one substring made chat retrieval miss a
    document that plainly contained several of its terms whenever the vector
    snapshot had not been built yet. Keyword search is the reliable fallback,
    so it must understand questions as words rather than exact prose.
    """
    words = [word for word in re.findall(r"[a-z0-9][a-z0-9_-]*", query.lower())
             if len(word) > 2 and word not in _SEARCH_STOP_WORDS]
    return [like_pattern(word) for word in dict.fromkeys(words)] or [like_pattern(query.strip())]


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
_vec_cache: dict[tuple[int, str], tuple[float, list[float] | None]] = {}
_vec_lock = threading.Lock()


def query_vector(query: str) -> list[float] | None:
    """The embedding for this query text — computed once, then reused for the
    short window in which a page and its count are fetched. None means the
    embedder had nothing to say, and every caller in that window agrees."""
    cache_key = (access.require_current_access().project_id, query)
    now = time.monotonic()
    with _vec_lock:
        hit = _vec_cache.get(cache_key)
        if hit and now - hit[0] < _VEC_TTL_SECONDS:
            return hit[1]
    vec = llm.embed(query)
    with _vec_lock:
        if len(_vec_cache) >= _VEC_CACHE_MAX:
            for stale in [k for k, (at, _) in _vec_cache.items()
                          if now - at >= _VEC_TTL_SECONDS] or [next(iter(_vec_cache))]:
                _vec_cache.pop(stale, None)
        _vec_cache[cache_key] = (now, vec)
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
    ranked_count = len(_rank_hybrid(query))
    ctx = access.require_current_access()
    if ctx.principal_type == "slack":
        return ranked_count
    patterns = keyword_patterns(query) if query.strip() else None
    return max(ranked_count, search_store.document_count(ctx.project_id, patterns))


# MUVERA/PolarQuant replaces the pgvector ANN half of the old CTE above. The
# keyword half remains canonical-content SQL during the Iceberg migration;
# both result lists are fused and cached together so `search` and
# `searchTotal` still describe exactly the same approximate candidate set.
_RANK_TTL_SECONDS = 120.0
_rank_cache: dict[tuple[t.Any, ...], tuple[float, list[dict]]] = {}
_rank_lock = threading.Lock()


def invalidate_search(project_id: int) -> None:
    """Drop ranked results immediately after a canonical document write."""
    with _rank_lock:
        for key in [key for key in _rank_cache if key and key[0] == int(project_id)]:
            _rank_cache.pop(key, None)


def _document_visible(row: dict, ctx: access.AccessContext) -> bool:
    """Project members see their project; Slack additionally honors channel ACLs.

    A verified Slack installation is already bound to exactly one project.
    Project and connector-scoped documents are therefore its shared knowledge
    base, while documents explicitly marked restricted still require the
    channel principal carried by the incoming event.
    """
    if ctx.principal_type != "slack":
        return True
    visibility = str(row.get("acl_visibility") or "project")
    if visibility in {"public", "project", "connector_scope"}:
        return True
    principals = row.get("acl_principals") or []
    if isinstance(principals, str):
        principals = jload(principals) or []
    return bool(ctx.principals.intersection(str(value) for value in principals))


def _keyword_score(row: dict, terms: list[str]) -> float:
    if not terms:
        return 1.0
    title = str(row.get("title") or "").lower()
    text = f"{row.get('snippet') or ''} {row.get('body') or ''}".lower()
    hits = sum(2 * title.count(term) + min(3, text.count(term)) for term in terms)
    return min(1.0, hits / max(1.0, len(terms) * 2.0))


def _rank_hybrid(query: str) -> list[dict]:
    ctx = access.require_current_access()
    project_id = ctx.project_id
    cache_key = (project_id, ctx.principal_type, ctx.principal_id,
                 tuple(sorted(ctx.principals)), query)
    now = time.monotonic()
    with _rank_lock:
        hit = _rank_cache.get(cache_key)
        if hit and now - hit[0] < _RANK_TTL_SECONDS:
            return hit[1]

    patterns = keyword_patterns(query)
    keyword_rows = search_store.keyword_candidates(
        project_id, patterns if query.strip() else None, MAX_K * 2,
    )

    semantic: dict[int, float] = {}
    vec = query_vector(query) if query.strip() else None
    if vec is not None:
        try:
            if retrieval.ensure_index():
                semantic = {h["document_id"]: h["score"] for h in retrieval.index_for(project_id).search(
                    np.asarray([vec], np.float32), k=ANN_CANDIDATES, candidate_k=1000)}
        except (OSError, ValueError):
            semantic = {}

    keyword_ids = {int(row["id"]) for row in keyword_rows}
    rows_by_id = {int(row["id"]): row for row in keyword_rows}
    missing = [doc_id for doc_id, score in semantic.items()
               if score > SIM_FLOOR and doc_id not in rows_by_id]
    if missing:
        for row in search_store.documents_by_id(project_id, missing):
            rows_by_id[int(row["id"])] = row

    terms = [word for word in re.findall(r"[a-z0-9][a-z0-9_-]*", query.lower()) if len(word) > 1]
    ranked = []
    for doc_id, row in rows_by_id.items():
        if not _document_visible(row, ctx):
            continue
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
        _rank_cache[cache_key] = (now, ranked)
    return ranked

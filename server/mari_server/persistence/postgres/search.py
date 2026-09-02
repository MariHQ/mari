"""PostgreSQL candidate retrieval for hybrid search."""

from __future__ import annotations

import re
import typing as t

from mari_server.persistence.postgres import connection as db


_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})
_STOP_WORDS = frozenset({
    "and", "are", "can", "company", "does", "for", "from", "how", "into", "our",
    "please", "tell", "work",
    "the", "their", "this", "was", "what", "when", "where", "which", "who",
    "why", "with", "you", "your",
})


def like_pattern(query: str) -> str:
    return f"%{query.translate(_LIKE_ESCAPE)}%"


def keyword_terms(query: str) -> list[str]:
    """Meaningful lexical terms shared by candidate selection and scoring."""
    return list(dict.fromkeys(
        word for word in re.findall(r"[a-z0-9][a-z0-9_-]*", query.lower())
        if len(word) > 2 and word not in _STOP_WORDS
    ))


def keyword_patterns(query: str) -> list[str]:
    words = keyword_terms(query)
    return [like_pattern(word) for word in words] or [like_pattern(query.strip())]


_TSQUERY_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_TSQUERY_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def prefix_tsquery(terms: t.Iterable[str]) -> str:
    """A to_tsquery input that prefix-matches each term: `auth:* | k8s:*`.

    websearch_to_tsquery matches stemmed whole words only, which lost the
    substring hits ILIKE used to give ("auth" no longer found
    "authentication"). to_tsquery accepts the `:*` prefix operator but is
    strict about syntax, so every term is reduced to [A-Za-z0-9_-] first:
    quotes, ampersands, pipes, parentheses and the like are stripped rather
    than escaped, and a term with nothing left is dropped. Hyphens survive
    because the parser handles them (`k8s-migration:*` becomes a phrase
    query, verified against Postgres 16), leading ones are trimmed so no term
    starts with punctuation."""
    cleaned = []
    for term in terms:
        term = _TSQUERY_UNSAFE.sub("", term).lstrip("_-")
        if term and term not in cleaned:
            cleaned.append(term)
    return " | ".join(f"{term}:*" for term in cleaned)


def search_text(query: str) -> str:
    """The to_tsquery input for a query: the same meaningful terms scoring
    uses, each as a prefix, OR-ed so any one of them admits a candidate,
    exactly as the ILIKE needle list did. A query with no such term ("AI",
    "the") falls back to the raw text's tokens of two characters or more (a
    one-letter prefix would admit most of the corpus); one with no usable
    token at all yields "" and the caller takes the ILIKE path."""
    terms = keyword_terms(query) or [
        token for token in _TSQUERY_TOKEN.findall(query) if len(token) >= 2
    ]
    return prefix_tsquery(terms)


def _keyword_predicate(conn, query: str | None) -> tuple[str, tuple]:
    """The candidate filter on `documents d`, as (sql, args).

    Candidates come from documents.search_vec, the stored tsvector with a GIN
    index (workflows.select_documents already reads it). The old `body ILIKE`
    needles could only ever be a sequential scan over every body in the
    project, and search paid it on every uncached query. ILIKE stays only as
    the fallback for text the parser reduces to nothing — a query made solely
    of tsquery stop words would otherwise match no document at all, where the
    literal substring still can. The count and the rows both go through here
    so they always describe the same match set.
    """
    if not query:
        return "", ()
    text = search_text(query)
    parsed = conn.execute(
        "SELECT numnode(to_tsquery('english', %s)) > 0 AS usable", (text,),
    ).fetchone() if text else None
    if parsed and parsed.get("usable"):
        return "AND d.search_vec @@ to_tsquery('english', %s)", (text,)
    return ("""AND EXISTS (SELECT 1 FROM unnest(%s::text[]) AS needle
                           WHERE d.title ILIKE needle OR d.snippet ILIKE needle
                              OR d.body ILIKE needle)""", (keyword_patterns(query),))


def document_count(project_id: int, query: str | None = None) -> int:
    with db.connect() as conn:
        predicate, args = _keyword_predicate(conn, query)
        row = conn.execute(
            f"SELECT count(*) AS n FROM documents d WHERE d.project_id = %s {predicate}",
            (project_id, *args),
        ).fetchone()
    return int((row or {}).get("n") or 0)


def keyword_candidates(project_id: int, query: str | None, limit: int) -> list[dict]:
    with db.connect() as conn:
        predicate, args = _keyword_predicate(conn, query)
        return conn.execute(
            f"""SELECT d.id, d.source, d.title, d.snippet, d.body, d.author,
                       d.author_initials, d.updated_src, d.kind, d.acl_visibility,
                       d.acl_principals, d.metadata,
                       array_remove(array_agg(t.tag), NULL) AS tags,
                       coalesce(max(td.search_weight), 1.0) AS boost
                  FROM documents d
                  LEFT JOIN tags t ON t.document_id = d.id AND t.project_id = d.project_id
                  LEFT JOIN tag_definitions td ON td.tag = t.tag
                 WHERE d.project_id = %s {predicate}
                 GROUP BY d.id ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
                 LIMIT %s""", (project_id, *args, limit),
        ).fetchall()


def documents_by_id(project_id: int, document_ids: list[int]) -> list[dict]:
    if not document_ids:
        return []
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.source, d.title, d.snippet, d.body, d.author,
                      d.author_initials, d.updated_src, d.kind, d.acl_visibility,
                      d.acl_principals, d.metadata,
                      array_remove(array_agg(t.tag), NULL) AS tags,
                      coalesce(max(td.search_weight), 1.0) AS boost
                 FROM documents d
                 LEFT JOIN tags t ON t.document_id = d.id AND t.project_id = d.project_id
                 LEFT JOIN tag_definitions td ON td.tag = t.tag
                WHERE d.project_id = %s AND d.id = ANY(%s) GROUP BY d.id""",
            (project_id, document_ids),
        ).fetchall()


def slack_channel_candidates(project_id: int, channel_name: str, limit: int) -> list[dict]:
    """Newest human Slack threads from one explicitly named channel."""
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.source, d.title, d.snippet, d.body, d.author,
                      d.author_initials, d.updated_src, d.kind, d.acl_visibility,
                      d.acl_principals, d.metadata,
                      array_remove(array_agg(t.tag), NULL) AS tags,
                      1.0 AS score
                 FROM documents d
                 LEFT JOIN tags t ON t.document_id = d.id AND t.project_id = d.project_id
                WHERE d.project_id = %s AND d.source = 'slack'
                  AND lower(d.metadata->>'channel_name') = lower(%s)
                GROUP BY d.id ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
                LIMIT %s""",
            (project_id, channel_name, max(1, min(int(limit), 50))),
        ).fetchall()

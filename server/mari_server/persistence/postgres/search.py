"""PostgreSQL candidate retrieval for hybrid search."""

from __future__ import annotations

from mari_server.persistence.postgres import connection as db
import re


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


def document_count(project_id: int, patterns: list[str] | None = None) -> int:
    with db.connect() as conn:
        if patterns:
            row = conn.execute(
                """SELECT count(*) AS n FROM documents
                   WHERE project_id = %s
                     AND EXISTS (SELECT 1 FROM unnest(%s::text[]) AS needle
                                 WHERE title ILIKE needle OR snippet ILIKE needle
                                    OR body ILIKE needle)""",
                (project_id, patterns),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT count(*) AS n FROM documents WHERE project_id = %s",
                (project_id,),
            ).fetchone()
    return int((row or {}).get("n") or 0)


def keyword_candidates(project_id: int, patterns: list[str] | None, limit: int) -> list[dict]:
    predicate = """AND EXISTS (SELECT 1 FROM unnest(%s::text[]) AS needle
                               WHERE d.title ILIKE needle OR d.snippet ILIKE needle
                                  OR d.body ILIKE needle)""" if patterns else ""
    args = (project_id, patterns, limit) if patterns else (project_id, limit)
    with db.connect() as conn:
        return conn.execute(
            f"""SELECT d.id, d.source, d.title, d.snippet, d.body, d.author,
                       d.author_initials, d.updated_src, d.kind, d.acl_visibility,
                       d.acl_principals, array_remove(array_agg(t.tag), NULL) AS tags,
                       coalesce(max(td.search_weight), 1.0) AS boost
                  FROM documents d
                  LEFT JOIN tags t ON t.document_id = d.id AND t.project_id = d.project_id
                  LEFT JOIN tag_definitions td ON td.tag = t.tag
                 WHERE d.project_id = %s {predicate}
                 GROUP BY d.id ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
                 LIMIT %s""", args,
        ).fetchall()


def documents_by_id(project_id: int, document_ids: list[int]) -> list[dict]:
    if not document_ids:
        return []
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.source, d.title, d.snippet, d.body, d.author,
                      d.author_initials, d.updated_src, d.kind, d.acl_visibility,
                      d.acl_principals, array_remove(array_agg(t.tag), NULL) AS tags,
                      coalesce(max(td.search_weight), 1.0) AS boost
                 FROM documents d
                 LEFT JOIN tags t ON t.document_id = d.id AND t.project_id = d.project_id
                 LEFT JOIN tag_definitions td ON td.tag = t.tag
                WHERE d.project_id = %s AND d.id = ANY(%s) GROUP BY d.id""",
            (project_id, document_ids),
        ).fetchall()

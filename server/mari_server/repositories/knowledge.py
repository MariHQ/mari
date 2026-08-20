"""PostgreSQL persistence for knowledge lifecycle operations."""

from __future__ import annotations

import json

from mari_server import db
from mari_server.domain import access


_SCAN_COLUMNS = {"facts": "facts_scanned_at", "decisions": "decisions_scanned_at"}


def scan_documents(kind: str, document_ids: list[int] | None, limit: int) -> list[dict]:
    column = _SCAN_COLUMNS[kind]
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        if document_ids:
            rows = conn.execute(
                f"""SELECT id, title, snippet, body, source, updated_src FROM documents
                     WHERE project_id = %s AND id = ANY(%s)
                     ORDER BY {column} NULLS FIRST, updated_src DESC NULLS LAST, id""",
                (project_id, list(document_ids)),
            ).fetchall()
            return rows[:limit] if limit else rows
        return conn.execute(
            f"""SELECT id, title, snippet, body, source, updated_src FROM documents
                 WHERE project_id = %s
                 ORDER BY {column} NULLS FIRST, updated_src DESC NULLS LAST, id
                 LIMIT %s""", (project_id, limit),
        ).fetchall()


def mark_scanned(kind: str, document_ids: list[int]) -> None:
    if not document_ids:
        return
    column = _SCAN_COLUMNS[kind]
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            f"UPDATE documents SET {column} = now() WHERE project_id = %s AND id = ANY(%s)",
            (project_id, list(document_ids)),
        )


def decision_statements() -> set[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT statement FROM decisions WHERE project_id = %s", (project_id,),
        ).fetchall()
    return {str(row["statement"]).lower() for row in rows}


def add_decision(statement: str, context: str, source: str, owner: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO decisions
               (project_id, statement, context, status, source_label, owners)
               VALUES (%s, %s, %s, 'proposed', %s, %s)
               ON CONFLICT (project_id, statement) DO NOTHING RETURNING id""",
            (project_id, statement, context, source, [owner]),
        ).fetchone()
    return bool(row)


def fact_claims(*, verified_only: bool = False) -> set[str]:
    project_id = access.require_current_access().project_id
    sql = "SELECT claim FROM facts WHERE project_id = %s"
    if verified_only:
        sql += " AND status = 'Verified'"
    with db.connect() as conn:
        rows = conn.execute(sql, (project_id,)).fetchall()
    return {str(row["claim"]).lower() for row in rows}


def add_fact(claim: str, source: str, owner: str, document_id: int) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO facts
               (project_id, claim, source, owner_name, owner_tint, status, verified, document_id)
               VALUES (%s, %s, %s, %s, 1, 'Needs review', '—', %s)
               ON CONFLICT (project_id, claim) DO NOTHING RETURNING id""",
            (project_id, claim, source, owner, document_id),
        ).fetchone()
    return bool(row)


def document(document_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE project_id = %s AND id = %s",
            (project_id, document_id),
        ).fetchone()


def add_finding(document_id: int, text: str, note: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO findings
               (project_id, document_id, kind, severity, text, note)
               VALUES (%s, %s, 'fact', 'error', %s, %s)
               ON CONFLICT (document_id, text) DO NOTHING RETURNING id""",
            (project_id, document_id, text, note),
        ).fetchone()
    return bool(row)


def recent_documents(limit: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT id, external_id, title, body, snippet, source, updated_src
                 FROM documents WHERE project_id = %s
                 ORDER BY updated_src DESC LIMIT %s""", (project_id, limit),
        ).fetchall()


def replace_digest(topics: list[tuple[str, str, str, str]]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("DELETE FROM digest_topics WHERE project_id = %s", (project_id,))
        for title, summary, wheres, impact in topics:
            conn.execute(
                """INSERT INTO digest_topics
                   (project_id, title, summary, wheres, impact)
                   VALUES (%s, %s, %s, %s, %s)""",
                (project_id, title, summary, wheres, impact),
            )

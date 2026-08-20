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


def add_fact(claim: str, source: str, owner: str, document_id: int | None) -> bool:
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


def set_task_done(task_id: int, done: bool) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "SELECT title FROM tasks WHERE project_id = %s AND id = %s",
            (project_id, task_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tasks SET done = %s WHERE project_id = %s AND id = %s",
                (done, project_id, task_id),
            )
    return str(row["title"]) if row else None


def clear_done_tasks() -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        rows = conn.execute(
            "DELETE FROM tasks WHERE project_id = %s AND done RETURNING id", (project_id,),
        ).fetchall()
    return len(rows)


def verify_fact(fact_id: int) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """UPDATE facts SET status = 'Verified', verified_at = current_date
                 WHERE project_id = %s AND id = %s RETURNING claim""",
            (project_id, fact_id),
        ).fetchone()
    return str(row["claim"]) if row else None


def create_task(*, title: str, assignee: str, initials: str, kind: str,
                kind_label: str, due_date: str | None, subject: tuple[str, ...]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO tasks
               (project_id, title, assignee, assignee_initials, assignee_tint, kind,
                kind_label, due_date, subject_type, subject_id, subject_title, subject_href)
               VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, title) DO NOTHING""",
            (project_id, title, assignee, initials, kind, kind_label, due_date, *subject),
        )


def set_task_due(task_id: int, due_date: str | None) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "SELECT title, due_date FROM tasks WHERE project_id = %s AND id = %s",
            (project_id, task_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tasks SET due_date = %s WHERE project_id = %s AND id = %s",
                (due_date, project_id, task_id),
            )
    return row


def document_exists(document_id: int) -> bool:
    return document(document_id) is not None


def upsert_glossary(*, term_id: int | None, term: str, definition: str,
                    owner: str, evidence: str, document_id: int | None) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if term_id:
            conn.execute(
                """UPDATE glossary SET term = %s, definition = %s, updated = now(),
                       evidence = CASE WHEN %s <> '' THEN %s ELSE evidence END,
                       evidence_doc_id = coalesce(%s, evidence_doc_id)
                     WHERE project_id = %s AND id = %s""",
                (term, definition, evidence, evidence, document_id, project_id, term_id),
            )
        else:
            conn.execute(
                """INSERT INTO glossary
                   (project_id, term, definition, owner_name, updated, evidence, evidence_doc_id)
                   VALUES (%s, %s, %s, %s, now(), %s, %s)
                   ON CONFLICT (project_id, term) DO UPDATE SET
                     definition = EXCLUDED.definition, updated = now(),
                     evidence = CASE WHEN EXCLUDED.evidence <> ''
                                     THEN EXCLUDED.evidence ELSE glossary.evidence END,
                     evidence_doc_id = coalesce(EXCLUDED.evidence_doc_id, glossary.evidence_doc_id)""",
                (project_id, term, definition, owner, evidence, document_id),
            )


def delete_glossary(term_id: int) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "DELETE FROM glossary WHERE project_id = %s AND id = %s RETURNING id",
            (project_id, term_id),
        ).fetchone()
    return bool(row)


def graph_views() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT id, name, state FROM graph_views WHERE project_id = %s ORDER BY id",
            (project_id,),
        ).fetchall()


def facts(document_id: int | None = None) -> list[dict]:
    project_id = access.require_current_access().project_id
    clause = " AND document_id = %s" if document_id is not None else ""
    args = (project_id, document_id) if document_id is not None else (project_id,)
    with db.connect() as conn:
        return conn.execute(
            f"SELECT * FROM facts WHERE project_id = %s{clause} ORDER BY id", args,
        ).fetchall()


def tasks() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT *, (due_date IS NOT NULL AND NOT done AND due_date < current_date) AS overdue
                 FROM tasks WHERE project_id = %s ORDER BY id""", (project_id,),
        ).fetchall()


def task_summary() -> tuple[dict | None, list[str], list[str]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        summary = conn.execute(
            """SELECT count(*) AS total, count(*) FILTER (WHERE NOT done) AS open,
                      count(*) FILTER (WHERE done) AS done,
                      count(*) FILTER (WHERE NOT done AND due_date < current_date) AS overdue,
                      count(*) FILTER (WHERE NOT done AND due_date >= current_date
                                       AND due_date <= current_date + 7) AS due_soon
                 FROM tasks WHERE project_id = %s""", (project_id,),
        ).fetchone()
        kinds = [row["kind_label"] for row in conn.execute(
            """SELECT DISTINCT kind_label FROM tasks
                 WHERE project_id = %s AND kind_label <> '' ORDER BY kind_label""",
            (project_id,),
        ).fetchall()]
        people = [row["assignee_initials"] for row in conn.execute(
            """SELECT DISTINCT assignee_initials FROM tasks
                 WHERE project_id = %s AND NOT done AND assignee_initials <> ''
                 ORDER BY assignee_initials""", (project_id,),
        ).fetchall()]
    return summary, kinds, people


def digest_topics() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM digest_topics WHERE project_id = %s ORDER BY id", (project_id,),
        ).fetchall()


def members() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT u.id, u.name, u.initials, u.tint, u.email, pm.role,
                      u.provider, pm.status, u.joined
                 FROM project_members pm JOIN users u ON u.id = pm.user_id
                WHERE pm.project_id = %s ORDER BY u.id""", (project_id,),
        ).fetchall()


def glossary_terms() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM glossary WHERE project_id = %s ORDER BY term", (project_id,),
        ).fetchall()


def tag_definitions() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.*, (SELECT count(*) FROM tags t
                              WHERE t.project_id = %s AND t.tag = d.tag) AS usage
                 FROM tag_definitions d ORDER BY d.search_weight DESC""", (project_id,),
        ).fetchall()


def style_guides() -> tuple[list[dict], list[dict]]:
    with db.connect() as conn:
        guides = conn.execute("SELECT * FROM style_guides ORDER BY sort, key").fetchall()
        rules = conn.execute(
            "SELECT guide_key, description FROM style_rules ORDER BY guide_key, sort, id",
        ).fetchall()
    return guides, rules


def style_rules(guide_key: str | None = None) -> list[dict]:
    with db.connect() as conn:
        if guide_key:
            return conn.execute(
                "SELECT * FROM style_rules WHERE guide_key = %s ORDER BY guide_key, sort, id",
                (guide_key,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM style_rules ORDER BY guide_key, sort, id",
        ).fetchall()


def setting_value(key: str):
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def document_templates() -> list[dict]:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM document_templates ORDER BY sort, key").fetchall()


def upload_manifest() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.source_path, d.external_id, d.updated_src,
                      count(c.id) AS chunks, count(c.embedding) AS embedded
                 FROM documents d
                 JOIN sources s ON s.project_id = d.project_id AND s.id = d.source_id
                               AND s.provider = 'upload'
                 LEFT JOIN chunks c ON c.project_id = d.project_id AND c.document_id = d.id
                WHERE d.project_id = %s GROUP BY d.id ORDER BY d.id""", (project_id,),
        ).fetchall()


def api_keys() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM api_keys WHERE project_id = %s ORDER BY id", (project_id,),
        ).fetchall()


def approved_answers() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT * FROM approved_answers WHERE project_id = %s
          ORDER BY (status = 'approved') DESC, served DESC""", (project_id,)).fetchall()


def answer_coverage_gaps(limit: int) -> list[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute("""WITH asked AS (
          SELECT lower(trim(detail)) AS question, max(at) AS last_at FROM usage_log
           WHERE project_id = %s AND kind = 'search' AND length(trim(detail)) >= 8 GROUP BY 1
          UNION ALL SELECT lower(trim(content)), max(created_at) FROM chat_messages
           WHERE project_id = %s AND role = 'user' AND length(trim(content)) BETWEEN 8 AND 200 GROUP BY 1)
          SELECT a.question, max(a.last_at) AS last_at FROM asked a
          WHERE NOT EXISTS (SELECT 1 FROM approved_answers ans WHERE ans.project_id = %s
            AND ans.status = 'approved' AND lower(ans.question) = a.question)
          GROUP BY a.question ORDER BY max(a.last_at) DESC LIMIT %s""",
          (project_id, project_id, project_id, limit)).fetchall()
    return [str(row["question"]) for row in rows]


def harvest_source_counts() -> tuple[list[dict], int]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        sources = conn.execute("SELECT source, count(*) AS n FROM documents WHERE project_id = %s GROUP BY source",
                               (project_id,)).fetchall()
        chat = conn.execute("SELECT count(*) AS n FROM chat_messages WHERE project_id = %s AND role = 'user'",
                            (project_id,)).fetchone()
    return sources, int(chat["n"])


def index_stats() -> dict:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT (SELECT count(*) FROM documents WHERE project_id = %s) AS docs,
          count(*) AS chunks, count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded
          FROM chunks WHERE project_id = %s""", (project_id, project_id)).fetchone()


def decisions_with_supersession() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT d.*, s.statement AS sup_stmt FROM decisions d
          LEFT JOIN decisions s ON s.project_id = d.project_id AND s.id = d.superseded_by
          WHERE d.project_id = %s ORDER BY d.id DESC""", (project_id,)).fetchall()


def readability() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT id, title, source, readability FROM documents WHERE project_id = %s ORDER BY id",
                            (project_id,)).fetchall()


def glossary_candidates() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT * FROM glossary WHERE project_id = %s AND candidate ORDER BY id",
                            (project_id,)).fetchall()

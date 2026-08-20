"""Project-scoped chat session and approved-answer persistence."""

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access
from mari_server.persistence.postgres.search import like_pattern


def live_destination(project_slug: str, destination_slug: str):
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.project_id, d.name, d.slug, d.title, d.welcome,
                      p.slug AS project_slug, p.name AS project_name
                 FROM knowledge_chat_destinations d JOIN projects p ON p.id = d.project_id
                WHERE p.slug = %s AND p.status = 'active'
                  AND d.slug = %s AND d.status = 'live'""",
            (project_slug, destination_slug),
        ).fetchone()


def create_session(project_id: int, owner_user_id: int | None, title: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO chat_sessions (project_id, owner_user_id, title)
                 VALUES (%s, %s, %s) RETURNING id""",
            (project_id, owner_user_id, title[:60]),
        ).fetchone()
        return int(row[0])


def session_exists(project_id: int, owner_user_id: int, session_id: int) -> bool:
    with db.connect() as conn:
        return bool(conn.execute(
            """SELECT id FROM chat_sessions WHERE id = %s AND project_id = %s
                 AND (owner_user_id = %s OR owner_user_id IS NULL)""",
            (session_id, project_id, owner_user_id),
        ).fetchone())


def add_message(project_id: int, session_id: int, role: str, content: str, sources=None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO chat_messages (project_id, session_id, role, content, sources)
                 VALUES (%s, %s, %s, %s, %s)""",
            (project_id, session_id, role, content, sources),
        )


def messages(project_id: int, session_id: int, limit: int = 12) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT role, content FROM chat_messages
                 WHERE project_id = %s AND session_id = %s ORDER BY id DESC LIMIT %s""",
            (project_id, session_id, limit),
        ).fetchall()
        return list(reversed(rows))


def approved_answer(project_id: int, question: str, vector: list[float] | None):
    with db.connect() as conn:
        row = None
        if vector:
            row = conn.execute(
                """SELECT id, question, answer, 1 - (embedding <=> %s::vector) AS sim
                     FROM approved_answers WHERE project_id = %s AND status = 'approved'
                       AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT 1""",
                (str(vector), project_id, str(vector)),
            ).fetchone()
            if row and row["sim"] < 0.62:
                row = None
        if row is None:
            row = conn.execute(
                """SELECT id, question, answer FROM approved_answers
                     WHERE project_id = %s AND status = 'approved'
                       AND (question ILIKE %s OR position(lower(question) in lower(%s)) > 0)
                     LIMIT 1""",
                (project_id, like_pattern(question[:60]), question),
            ).fetchone()
        if row:
            conn.execute(
                "UPDATE approved_answers SET served = served + 1 WHERE project_id = %s AND id = %s",
                (project_id, row["id"]),
            )
        return row


def verified_facts(project_id: int, limit: int = 8) -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT claim FROM facts WHERE project_id = %s AND status = 'Verified' LIMIT %s",
            (project_id, limit),
        ).fetchall()


def sessions_for_owner(user_id: int, limit: int = 20) -> list[tuple[dict, list[dict]]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        sessions = conn.execute("""SELECT * FROM chat_sessions
          WHERE project_id = %s AND owner_user_id = %s ORDER BY id DESC LIMIT %s""",
          (project_id, user_id, limit)).fetchall()
        return [(session, conn.execute("""SELECT * FROM chat_messages
          WHERE project_id = %s AND session_id = %s ORDER BY id""",
          (project_id, session["id"])).fetchall()) for session in sessions]

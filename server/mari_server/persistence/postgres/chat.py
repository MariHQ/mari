"""Project-scoped chat session and approved-answer persistence."""

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access
from mari_server.persistence.postgres.search import like_pattern


def live_destination(project_slug: str, destination_slug: str):
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.project_id, d.name, d.slug, d.title, d.welcome, d.tools,
                      p.slug AS project_slug, p.name AS project_name
                 FROM knowledge_chat_destinations d JOIN projects p ON p.id = d.project_id
                WHERE p.slug = %s AND p.status = 'active'
                  AND d.slug = %s AND d.status = 'live'""",
            (project_slug, destination_slug),
        ).fetchone()


def create_session(project_id: int, owner_user_id: int | None, title: str,
                   public_token: str | None = None) -> int:
    """A session with no owner is a public knowledge-chat one; it carries the
    token its visitor has to echo back, because the sequential id alone is
    guessable (migration 0034)."""
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO chat_sessions (project_id, owner_user_id, title, public_token)
                 VALUES (%s, %s, %s, %s) RETURNING id""",
            (project_id, owner_user_id, title[:60], public_token),
        ).fetchone()
        return int(row["id"])


def session_exists(project_id: int, owner_user_id: int, session_id: int) -> bool:
    """A signed-in caller continues only a session they own. Ownerless rows are
    public visitors' conversations and never attach to an account, however
    the id was obtained."""
    with db.connect() as conn:
        return bool(conn.execute(
            """SELECT id FROM chat_sessions WHERE id = %s AND project_id = %s
                 AND owner_user_id = %s""",
            (session_id, project_id, owner_user_id),
        ).fetchone())


def public_session_exists(project_id: int, session_id: int, public_token: str) -> bool:
    """The id and the token both have to match. Rows created before the token
    column have NULL there and are not continuable at all."""
    with db.connect() as conn:
        return bool(conn.execute(
            """SELECT id FROM chat_sessions WHERE id = %s AND project_id = %s
                 AND owner_user_id IS NULL AND public_token IS NOT NULL
                 AND public_token = %s""",
            (session_id, project_id, public_token),
        ).fetchone())


def answers_since(project_id: int, usage_detail: str, hours: int = 24) -> int:
    """Answers one surface produced in the window, from the same usage_log rows
    log_usage('chat_answer', detail) writes. Counting the log rather than a
    process-local counter keeps the budget honest across several instances."""
    with db.connect() as conn:
        row = conn.execute(
            """SELECT count(*) AS n FROM usage_log
                WHERE project_id = %s AND kind = 'chat_answer' AND detail = %s
                  AND at > now() - make_interval(hours => %s)""",
            (project_id, usage_detail[:120], int(hours)),
        ).fetchone()
        return int(row["n"]) if row else 0


def add_message(project_id: int, session_id: int, role: str, content: str, sources=None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO chat_messages (project_id, session_id, role, content, sources)
                 VALUES (%s, %s, %s, %s, %s)""",
            (project_id, session_id, role, content, sources if sources is not None else "[]"),
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

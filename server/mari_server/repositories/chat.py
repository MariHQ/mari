"""Chat session persistence."""

from mari_server import db
from mari_server.domain import access


def sessions_for_owner(user_id: int, limit: int = 20) -> list[tuple[dict, list[dict]]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        sessions = conn.execute("""SELECT * FROM chat_sessions
          WHERE project_id = %s AND owner_user_id = %s ORDER BY id DESC LIMIT %s""",
          (project_id, user_id, limit)).fetchall()
        return [(session, conn.execute("""SELECT * FROM chat_messages
          WHERE project_id = %s AND session_id = %s ORDER BY id""",
          (project_id, session["id"])).fetchall()) for session in sessions]

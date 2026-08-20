"""Workspace settings, member provisioning, and notifications persistence."""

from mari_server import db
from mari_server.domain import access


def notifications(user_name: str) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT * FROM notifications
          WHERE project_id = %s AND user_name = %s ORDER BY id""", (project_id, user_name)).fetchall()


def value(key: str):
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE project_id = %s AND key = %s",
                           (project_id, key)).fetchone()
    return row["value"] if row else None


def all_settings() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT * FROM settings WHERE project_id = %s ORDER BY key", (project_id,)).fetchall()


def github_member_count() -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute("""SELECT count(*) AS n FROM project_members pm JOIN users u ON u.id = pm.user_id
          WHERE pm.project_id = %s AND pm.status = 'active' AND u.provider = 'github'""", (project_id,)).fetchone()
    return int(row["n"])

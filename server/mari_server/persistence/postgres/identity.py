"""Identity and membership persistence."""

from mari_server.persistence.postgres import connection as db


def memberships(user_id: int) -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""SELECT p.id AS project_id, p.slug AS project_slug,
          p.name AS project_name, pm.role, pm.status FROM project_members pm
          JOIN projects p ON p.id = pm.project_id WHERE pm.user_id = %s
          AND pm.status = 'active' AND p.status = 'active' ORDER BY p.id""", (user_id,)).fetchall()


def authenticate_api_key(token_hash: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT k.id, k.project_id, k.scopes, p.slug, p.name AS project_name
          FROM api_keys k JOIN projects p ON p.id = k.project_id
          WHERE k.token_hash = %s AND NOT k.revoked AND p.status = 'active'""", (token_hash,)).fetchone()


def touch_api_key(key_id: int) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE api_keys SET last_used = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s",
                     (key_id,))

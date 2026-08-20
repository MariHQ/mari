"""Administrative persistence operations."""

from __future__ import annotations

import hashlib
import json

from mari_server import db
from mari_server.domain import access


def connect_source(provider: str, name: str, config: dict) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO sources
          (project_id, provider, display_name, status, stat_num, stat_unit, bars, config, docs_count, health)
          VALUES (%s, %s, %s, 'active', '0', 'items', '{}', %s, 0, 'Never synced')
          ON CONFLICT (project_id, provider) DO UPDATE SET config = sources.config || EXCLUDED.config,
          status = 'active'""", (project_id, provider, name, json.dumps(config)))
        conn.execute("""INSERT INTO sync_events (project_id, provider, event, detail, at_label)
          VALUES (%s, %s, %s, '', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
          (project_id, provider, f"connected: {name}"))


def pause_source(provider: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE sources SET status = 'paused', health = 'Paused' WHERE project_id = %s AND provider = %s",
                     (project_id, provider))
        conn.execute("""UPDATE ingest_checkpoints SET status = 'paused'
          WHERE project_id = %s AND provider = %s AND status = 'running'""", (project_id, provider))
        conn.execute("""INSERT INTO sync_events (project_id, provider, event, detail, at_label)
          VALUES (%s, %s, 'paused', 'Paused by admin',
          to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""", (project_id, provider))


def change_member_role(user_id: int, role: str) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        member = conn.execute("""SELECT u.name, u.email, pm.role FROM project_members pm
          JOIN users u ON u.id = pm.user_id WHERE pm.project_id = %s AND pm.user_id = %s""",
          (project_id, user_id)).fetchone()
        if not member:
            return None
        if member["role"] in ("owner", "admin") and role not in ("owner", "admin"):
            others = conn.execute("""SELECT count(*) AS n FROM project_members WHERE project_id = %s
              AND user_id <> %s AND status = 'active' AND role IN ('owner','admin')""",
              (project_id, user_id)).fetchone()["n"]
            if not others:
                raise ValueError("This is the only admin — promote someone else first.")
        conn.execute("UPDATE project_members SET role = %s WHERE project_id = %s AND user_id = %s",
                     (role, project_id, user_id))
    return member


def remove_member(user_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        member = conn.execute("""SELECT u.name, u.email, u.provider, pm.role FROM project_members pm
          JOIN users u ON u.id = pm.user_id WHERE pm.project_id = %s AND pm.user_id = %s""",
          (project_id, user_id)).fetchone()
        if not member:
            return None
        if member["role"] in ("owner", "admin"):
            others = conn.execute("""SELECT count(*) AS n FROM project_members WHERE project_id = %s
              AND user_id <> %s AND status = 'active' AND role IN ('owner','admin')""",
              (project_id, user_id)).fetchone()["n"]
            if not others:
                raise ValueError("This is the only admin — promote someone else first.")
        conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                     (project_id, user_id))
    return member


def create_api_key(name: str, token: str, scopes: list[str]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if conn.execute("SELECT 1 FROM api_keys WHERE project_id = %s AND name = %s",
                        (project_id, name)).fetchone():
            raise ValueError(f"An API key called '{name}' already exists.")
        conn.execute("""INSERT INTO api_keys
          (project_id, name, prefix, token_hash, scopes, created_at, last_used)
          VALUES (%s, %s, %s, %s, %s, now(), 'never')""",
          (project_id, name, token[:12] + "…", hashlib.sha256(token.encode()).hexdigest(), ",".join(scopes)))


def revoke_api_key(key_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        return conn.execute("""UPDATE api_keys SET revoked = true, token_hash = ''
          WHERE project_id = %s AND id = %s RETURNING name, prefix, scopes""",
          (project_id, key_id)).fetchone()


def save_setting(key: str, value) -> object | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        before = conn.execute("SELECT value FROM settings WHERE project_id = %s AND key = %s",
                              (project_id, key)).fetchone()
        conn.execute("""INSERT INTO settings (project_id, key, value) VALUES (%s, %s, %s)
          ON CONFLICT (project_id, key) DO UPDATE SET value = EXCLUDED.value""",
          (project_id, key, json.dumps(value)))
    return before["value"] if before else None


def setting(key: str) -> object | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE project_id = %s AND key = %s",
                           (project_id, key)).fetchone()
    return row["value"] if row else None


def merge_setting(key: str, values: dict) -> object | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        before = conn.execute("SELECT value FROM settings WHERE project_id = %s AND key = %s",
                              (project_id, key)).fetchone()
        conn.execute("""INSERT INTO settings (project_id, key, value) VALUES (%s, %s, %s)
          ON CONFLICT (project_id, key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
          (project_id, key, json.dumps(values)))
    return before["value"] if before else None


def add_github_source(repo: str, config: dict) -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if conn.execute("""SELECT id FROM sources WHERE project_id = %s AND kind = 'connector'
          AND split_part(provider, ':', 1) = 'github' AND lower(config->>'repo') = lower(%s)""",
          (project_id, repo)).fetchone():
            raise ValueError(f"Repository {repo} is already connected")
        row = conn.execute("""INSERT INTO sources
          (project_id, provider, display_name, kind, status, stat_num, stat_unit, bars, config, docs_count, health)
          VALUES (%s, %s, %s, 'connector', 'active', '0', 'docs', '{}', %s, 0, 'Syncing') RETURNING id""",
          (project_id, f"github:{repo}", repo, json.dumps(config))).fetchone()
    return int(row["id"])


def source(provider: str) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("SELECT id, kind, config FROM sources WHERE project_id = %s AND provider = %s",
                            (project_id, provider)).fetchone()


def update_source_config(provider: str, config: dict) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute("SELECT config FROM sources WHERE project_id = %s AND provider = %s",
                           (project_id, provider)).fetchone()
        if row:
            conn.execute("UPDATE sources SET config = config || %s::jsonb WHERE project_id = %s AND provider = %s",
                         (json.dumps(config), project_id, provider))
    return row


def open_audit_finding_ids(run_id: int, kind: str) -> list[int]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return [int(row["id"]) for row in conn.execute("""SELECT id FROM audit_findings
          WHERE project_id = %s AND run_id = %s AND kind = %s AND status = 'open'""",
          (project_id, run_id, kind)).fetchall()]


def dismiss_audit_finding(finding_id: int) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE audit_findings SET status = 'dismissed' WHERE project_id = %s AND id = %s",
                     (project_id, finding_id))

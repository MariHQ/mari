"""Bot installation, conversation, and answer-canon persistence."""

from __future__ import annotations

import json

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access


def setting(key: str) -> dict:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE project_id = %s AND key = %s",
                           (project_id, key)).fetchone()
    value = row["value"] if row else {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def merge_setting(key: str, patch: dict) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO settings (project_id, key, value) VALUES (%s, %s, %s)
          ON CONFLICT (project_id, key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
          (project_id, key, json.dumps(patch)))


def log_usage(kind: str, detail: str = "") -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("INSERT INTO usage_log (project_id, kind, detail) VALUES (%s, %s, %s)",
                     (project_id, kind, detail))


def approved_answer(vector: list[float]) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT question, answer, 1 - (embedding <=> %s::vector) AS sim
          FROM approved_answers WHERE project_id = %s AND status = 'approved' AND embedding IS NOT NULL
          ORDER BY embedding <=> %s::vector LIMIT 1""", (str(vector), project_id, str(vector))).fetchone()


def verified_facts(limit: int = 8) -> list[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return [str(row["claim"]) for row in conn.execute(
            "SELECT claim FROM facts WHERE project_id = %s AND status = 'Verified' LIMIT %s",
            (project_id, limit)).fetchall()]


def verified_fact_ids(claims: list[str]) -> dict[str, int]:
    """Resolve only cited claims back to ledger identities for impact lineage."""
    if not claims:
        return {}
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT id, claim FROM facts
                WHERE project_id = %s AND status = 'Verified' AND claim = ANY(%s)""",
            (project_id, claims),
        ).fetchall()
    return {str(row["claim"]): int(row["id"]) for row in rows}


def touch_installation(installation_id: int, patch: dict | None = None) -> None:
    with db.connect() as conn, conn.transaction():
        if patch:
            conn.execute("UPDATE bot_installations SET config = config || %s, updated_at = now() WHERE id = %s",
                         (json.dumps(patch), installation_id))
        else:
            conn.execute("UPDATE bot_installations SET updated_at = now() WHERE id = %s", (installation_id,))


def slack_sources(project_id: int) -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""SELECT id, config FROM sources WHERE project_id = %s AND kind = 'connector'
          AND split_part(provider, ':', 1) = 'slack' AND status = 'active'""", (project_id,)).fetchall()


def installation(installation_id: int, project_id: int) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT b.*, p.slug AS project_slug, p.name AS project_name
          FROM bot_installations b JOIN projects p ON p.id = b.project_id
          WHERE b.id = %s AND b.project_id = %s AND b.provider = 'slack'
          AND b.status = 'connected' AND p.status = 'active'""", (installation_id, project_id)).fetchone()


def installation_by_team(team_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT b.*, p.slug AS project_slug, p.name AS project_name
          FROM bot_installations b JOIN projects p ON p.id = b.project_id
          WHERE b.provider = 'slack' AND b.external_team_id = %s
          AND b.status = 'connected' AND p.status = 'active'""", (team_id,)).fetchone()


def thread(installation_id: int, project_id: int, channel: str, thread_ts: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT conversation FROM slack_bot_threads
          WHERE installation_id = %s AND project_id = %s AND channel_id = %s AND thread_ts = %s""",
          (installation_id, project_id, channel, thread_ts)).fetchone()


def thread_exists(installation_id: int, project_id: int, channel: str, thread_ts: str) -> bool:
    return thread(installation_id, project_id, channel, thread_ts) is not None


def save_thread(installation_id: int, project_id: int, channel: str, thread_ts: str,
                bot_message_ts: str, conversation: list[dict]) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO slack_bot_threads
          (installation_id, project_id, channel_id, thread_ts, bot_message_ts, conversation)
          VALUES (%s, %s, %s, %s, %s, %s)
          ON CONFLICT (installation_id, channel_id, thread_ts) DO UPDATE
          SET bot_message_ts = EXCLUDED.bot_message_ts, conversation = EXCLUDED.conversation,
          last_event_at = now()""", (installation_id, project_id, channel, thread_ts,
                                      bot_message_ts, json.dumps(conversation)))


def status(project_id: int) -> tuple[dict, dict, list[dict]]:
    with db.connect() as conn:
        slack = conn.execute("""SELECT config FROM bot_installations WHERE project_id = %s
          AND provider = 'slack' AND status = 'connected' ORDER BY id LIMIT 1""", (project_id,)).fetchone()
        github = conn.execute("""SELECT config FROM bot_installations WHERE project_id = %s
          AND provider = 'github' AND status = 'connected' ORDER BY id LIMIT 1""", (project_id,)).fetchone()
        repos = conn.execute("""SELECT id, config->>'repo' AS repo FROM sources WHERE project_id = %s
          AND kind = 'connector' AND split_part(provider, ':', 1) = 'github'
          AND config->>'repo' IS NOT NULL ORDER BY id""", (project_id,)).fetchall()
    return (slack or {}).get("config") or {}, (github or {}).get("config") or {}, repos


def configure_slack(project_id: int, team_id: str, config: dict) -> int:
    with db.connect() as conn, conn.transaction():
        current = conn.execute("""SELECT id FROM bot_installations WHERE project_id = %s
          AND provider = 'slack' ORDER BY id LIMIT 1 FOR UPDATE""", (project_id,)).fetchone()
        owner = conn.execute("""SELECT id, project_id FROM bot_installations WHERE provider = 'slack'
          AND external_team_id = %s AND external_installation_id = '' FOR UPDATE""", (team_id,)).fetchone()
        if owner and owner["project_id"] != project_id:
            raise ValueError("That Slack workspace is already connected to another project.")
        if current:
            row = conn.execute("""UPDATE bot_installations SET external_team_id = %s,
              external_installation_id = '', config = config || %s, status = 'connected', updated_at = now()
              WHERE id = %s AND project_id = %s RETURNING id""",
              (team_id, json.dumps(config), current["id"], project_id)).fetchone()
        else:
            row = conn.execute("""INSERT INTO bot_installations
              (project_id, provider, external_team_id, external_installation_id, config, status)
              VALUES (%s, 'slack', %s, '', %s, 'connected') RETURNING id""",
              (project_id, team_id, json.dumps(config))).fetchone()
    return int(row["id"])


def project_slack(project_id: int) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT id, config FROM bot_installations WHERE project_id = %s
          AND provider = 'slack' AND status = 'connected' ORDER BY id LIMIT 1""", (project_id,)).fetchone()


def socket_installations() -> list[dict]:
    """Active Slack installations configured for direct Socket Mode events."""
    with db.connect() as conn:
        return conn.execute("""SELECT id, project_id, config FROM bot_installations
          WHERE provider = 'slack' AND status = 'connected'
          AND coalesce(config->>'app_token', '') LIKE 'xapp-%'
          AND coalesce(config->>'bot_token', '') LIKE 'xoxb-%'
          ORDER BY id""").fetchall()

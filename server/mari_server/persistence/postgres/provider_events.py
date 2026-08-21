"""Webhook routing and provider watch persistence."""

from __future__ import annotations

import datetime as dt
import json

from mari_server.persistence.postgres import connection as db


def github_installation(external_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT b.*, p.slug AS project_slug, p.name AS project_name
          FROM bot_installations b JOIN projects p ON p.id = b.project_id
          WHERE b.provider = 'github' AND b.external_installation_id = %s
          AND b.status = 'connected' AND p.status = 'active'""", (external_id,)).fetchone()


def github_source(project_id: int, repository: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("""SELECT id FROM sources WHERE project_id = %s AND kind = 'connector'
          AND split_part(provider, ':', 1) = 'github' AND config->>'repo' = %s
          AND COALESCE(status, 'active') <> 'disconnected'""", (project_id, repository)).fetchone()


def confluence_source(source_id: int, project_id: int | None = None) -> dict | None:
    with db.connect() as conn:
        if project_id is None:
            return conn.execute("""SELECT s.*, p.slug AS project_slug, p.name AS project_name
              FROM sources s JOIN projects p ON p.id = s.project_id WHERE s.id = %s
              AND s.kind = 'connector' AND s.provider = 'confluence'
              AND COALESCE(s.status, 'active') <> 'disconnected' AND p.status = 'active'""",
              (source_id,)).fetchone()
        return conn.execute("""SELECT s.*, p.slug AS project_slug, p.name AS project_name
          FROM sources s JOIN projects p ON p.id = s.project_id WHERE s.id = %s
          AND s.project_id = %s AND s.kind = 'connector' AND s.provider = 'confluence'
          AND COALESCE(s.status, 'active') <> 'disconnected' AND p.status = 'active'""",
          (source_id, project_id)).fetchone()


def source(source_id: int, project_id: int, kind: str, provider: str | None = None) -> dict | None:
    sql = """SELECT s.*, p.slug AS project_slug, p.name AS project_name FROM sources s
      JOIN projects p ON p.id = s.project_id WHERE s.id = %s AND s.project_id = %s AND s.kind = %s
      AND COALESCE(s.status, 'active') <> 'disconnected' AND p.status = 'active'"""
    args: tuple = (source_id, project_id, kind)
    if provider:
        sql += " AND split_part(s.provider, ':', 1) = %s"
        args += (provider,)
    with db.connect() as conn:
        return conn.execute(sql, args).fetchone()


def installation_active(installation_id: int, project_id: int, provider: str) -> bool:
    with db.connect() as conn:
        return bool(conn.execute("""SELECT 1 FROM bot_installations WHERE id = %s AND project_id = %s
          AND provider = %s AND status = 'connected'""", (installation_id, project_id, provider)).fetchone())


def create_drive_watch(project_id: int, source_id: int, channel_id: str,
                       token_hash: str, page_token: str, expiration: dt.datetime) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("""INSERT INTO gdrive_watch_channels
          (project_id, source_id, channel_id, token_hash, page_token, expiration)
          VALUES (%s, %s, %s, %s, %s, %s)""",
          (project_id, source_id, channel_id, token_hash, page_token, expiration))


def update_drive_watch(channel_id: str, **values) -> None:
    allowed = {"resource_id", "expiration", "status", "last_error", "page_token"}
    items = [(key, value) for key, value in values.items() if key in allowed]
    if not items:
        return
    clause = ", ".join(f"{key} = %s" for key, _ in items)
    with db.connect() as conn, conn.transaction():
        conn.execute(f"UPDATE gdrive_watch_channels SET {clause}, updated_at = now() WHERE channel_id = %s",
                     tuple(value for _, value in items) + (channel_id,))


def activate_drive_watch(channel_id: str, source_id: int, resource_id: str,
                         expiration: dt.datetime) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("""UPDATE gdrive_watch_channels SET resource_id = %s, expiration = %s,
          status = 'active', updated_at = now() WHERE channel_id = %s""",
          (resource_id, expiration, channel_id))
        conn.execute("""UPDATE gdrive_watch_channels SET status = 'retiring', updated_at = now()
          WHERE source_id = %s AND channel_id <> %s AND status = 'active'""", (source_id, channel_id))


def drive_channel(channel_id: str, project_id: int | None = None) -> dict | None:
    with db.connect() as conn:
        if project_id is None:
            return conn.execute("""SELECT c.*, s.status AS source_status, p.status AS project_status
              FROM gdrive_watch_channels c JOIN sources s ON s.id = c.source_id
              JOIN projects p ON p.id = c.project_id WHERE c.channel_id = %s
              AND c.status IN ('creating','active','retiring')""", (channel_id,)).fetchone()
        return conn.execute("""SELECT c.*, s.config, s.provider, s.display_name,
          s.status AS source_status, p.slug AS project_slug, p.name AS project_name,
          p.status AS project_status FROM gdrive_watch_channels c JOIN sources s ON s.id = c.source_id
          JOIN projects p ON p.id = c.project_id WHERE c.channel_id = %s AND c.project_id = %s""",
          (channel_id, project_id)).fetchone()


def observe_drive_message(channel_id: str, resource_id: str, number: int) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("""UPDATE gdrive_watch_channels SET resource_id = CASE WHEN resource_id = '' THEN %s
          ELSE resource_id END, last_message_number = GREATEST(last_message_number, %s), updated_at = now()
          WHERE channel_id = %s""", (resource_id, number, channel_id))


def update_drive_cursor(source_id: int, config: dict, page_token: str) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE sources SET config = %s, last_sync_at = now(), health = 'Healthy' WHERE id = %s",
                     (json.dumps(config), source_id))
        conn.execute("""UPDATE gdrive_watch_channels SET page_token = %s, last_error = '', updated_at = now()
          WHERE source_id = %s""", (page_token, source_id))


def mark_drive_resync(channel_id: str) -> None:
    update_drive_watch(channel_id, status="needs_full_resync", last_error="changes token expired (HTTP 410)")


def restore_drive_cursor(source_id: int, page_token: str) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute("""UPDATE gdrive_watch_channels SET page_token = %s, status = 'active',
          last_error = '', updated_at = now() WHERE source_id = %s
          AND status IN ('active','needs_full_resync','retiring')""", (page_token, source_id))


def due_drive_watches(deadline: dt.datetime) -> list[dict]:
    with db.connect() as conn, conn.transaction():
        conn.execute("""DELETE FROM gdrive_watch_channels WHERE status = 'retiring'
          AND expiration IS NOT NULL AND expiration < now()""")
        return conn.execute("""SELECT c.source_id, c.project_id, p.slug, p.name
          FROM gdrive_watch_channels c JOIN projects p ON p.id = c.project_id
          WHERE c.status = 'active' AND c.expiration IS NOT NULL AND c.expiration <= %s
          AND p.status = 'active' ORDER BY c.expiration""", (deadline,)).fetchall()

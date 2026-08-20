"""Persistence operations for connector sources and sync state."""

from __future__ import annotations

from mari_server import db
from mari_server.domain import access
import json


def pulse_inputs() -> tuple[list[dict], list[dict], list[dict]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        daily = conn.execute("""SELECT source_id, updated_src AS day, count(*) AS n FROM documents
          WHERE project_id = %s AND source_id IS NOT NULL AND updated_src >= current_date - 11
          GROUP BY source_id, updated_src""", (project_id,)).fetchall()
        workflows = conn.execute(
            "SELECT id, status, nodes, trigger FROM workflows WHERE project_id = %s", (project_id,),
        ).fetchall()
        sources = conn.execute(
            "SELECT * FROM sources WHERE project_id = %s ORDER BY id", (project_id,),
        ).fetchall()
    return daily, workflows, sources


def github_configs() -> tuple[dict | None, list[dict]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        latest = conn.execute("""SELECT config FROM sources WHERE project_id = %s
          AND kind = 'connector' AND split_part(provider, ':', 1) = 'github'
          AND config->>'token' <> '' ORDER BY id DESC LIMIT 1""", (project_id,)).fetchone()
        connected = conn.execute("""SELECT config FROM sources WHERE project_id = %s
          AND kind = 'connector' AND split_part(provider, ':', 1) = 'github'""", (project_id,)).fetchall()
    return latest, connected


def sync_summary(source_id: int) -> tuple[dict | None, dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        source = conn.execute("""SELECT kind, config, last_sync_at FROM sources
          WHERE project_id = %s AND id = %s""", (project_id, source_id)).fetchone()
        counts = conn.execute("""SELECT count(DISTINCT d.id) AS docs, count(c.id) AS chunks,
          count(c.id) FILTER (WHERE c.embedding IS NOT NULL) AS embedded
          FROM documents d LEFT JOIN chunks c ON c.project_id = d.project_id AND c.document_id = d.id
          WHERE d.project_id = %s AND d.source_id = %s""", (project_id, source_id)).fetchone()
    return source, counts or {"docs": 0, "chunks": 0, "embedded": 0}


def checkpoints() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM ingest_checkpoints WHERE project_id = %s ORDER BY id", (project_id,),
        ).fetchall()


def sync_events(limit: int = 12) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM sync_events WHERE project_id = %s ORDER BY id DESC LIMIT %s", (project_id, limit),
        ).fetchall()


def freshness() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""WITH bucketed AS (
          SELECT d.source_id, CASE WHEN d.updated_src IS NULL OR d.updated_src < current_date - 30
            OR EXISTS (SELECT 1 FROM tags t WHERE t.project_id = d.project_id
              AND t.document_id = d.id AND t.tag = 'stale') THEN 'stale'
            WHEN d.updated_src < current_date - 7 THEN 'aging' ELSE 'fresh' END AS bucket
          FROM documents d WHERE d.project_id = %s AND d.source_id IS NOT NULL)
          SELECT s.display_name AS source, s.provider,
            count(*) FILTER (WHERE b.bucket = 'fresh') AS fresh,
            count(*) FILTER (WHERE b.bucket = 'aging') AS aging,
            count(*) FILTER (WHERE b.bucket = 'stale') AS stale
          FROM sources s LEFT JOIN bucketed b ON b.source_id = s.id
          WHERE s.project_id = %s GROUP BY s.id, s.display_name, s.provider
          ORDER BY count(b.source_id) DESC, s.id""", (project_id, project_id)).fetchall()


def connector_sources() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute("""SELECT id, kind, provider, config FROM sources
          WHERE project_id = %s AND kind = 'connector' ORDER BY id""", (project_id,)).fetchall()


def add_connector(provider: str, display_name: str, config: dict) -> int | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        if conn.execute("""SELECT id FROM sources WHERE project_id = %s
          AND kind = 'connector' AND provider = %s""", (project_id, provider)).fetchone():
            return None
        row = conn.execute("""INSERT INTO sources
          (project_id, provider, display_name, kind, status, stat_num, stat_unit, bars, config, docs_count, health)
          VALUES (%s, %s, %s, 'connector', 'active', '0', 'docs', '{}', %s, 0, 'Syncing') RETURNING id""",
          (project_id, provider, display_name, json.dumps(config))).fetchone()
        conn.execute("""INSERT INTO sync_events (project_id, provider, event, detail, at_label)
          VALUES (%s, %s, %s, '', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
          (project_id, provider, f"connected: {display_name}"))
    return int(row["id"])

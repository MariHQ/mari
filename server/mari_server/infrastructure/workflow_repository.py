"""Postgres, audit, and executor adapters for workflow use cases."""

from __future__ import annotations

import json

from mari_server.infrastructure import workflow_runtime as flowengine
from mari_server.infrastructure.database import audit, jload, transaction
from mari_server.application.workflows import WorkflowPorts


def _create_run(project_id: int, workflow_id: int, dry_run: bool):
    def create(conn):
        row = conn.execute(
            """INSERT INTO workflow_runs
                 (project_id, workflow_id, number, status, started_label, duration, progress, stats, rows_data)
               SELECT %s, id, nextval('workflow_run_number_seq'), 'running',
                      to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]'
                 FROM workflows WHERE project_id = %s AND id = %s
               RETURNING id, number,
                 (SELECT name FROM workflows WHERE project_id = %s AND id = %s) AS name""",
            (project_id, json.dumps({"ctx": {"dry_run": True}, "dry_run": True} if dry_run else {}),
             project_id, workflow_id, project_id, workflow_id),
        ).fetchone()
        return (int(row["id"]), int(row["number"]), str(row["name"])) if row else None
    return transaction(create)


def _approve(project_id: int, run_id: int, actor: str):
    def approve(conn):
        run = conn.execute(
            """SELECT * FROM workflow_runs WHERE project_id = %s AND id = %s
                 FOR UPDATE""", (project_id, run_id),
        ).fetchone()
        if not run or run["status"] != "waiting":
            return None
        stats, rows = jload(run["stats"]) or {}, jload(run["rows_data"]) or []
        paused_at = int(stats.get("paused_at", 0))
        if paused_at < len(rows):
            rows[paused_at].update(status="passed", detail=f"approved by {actor}")
        conn.execute(
            """UPDATE workflow_runs SET rows_data = %s, status = 'running'
                 WHERE project_id = %s AND id = %s""",
            (json.dumps(rows), project_id, run_id),
        )
        return int(run["number"]), paused_at + 1
    return transaction(approve)


def _save(project_id, name, description, steps, workflow_id, color, pinned):
    def save(conn):
        if workflow_id is not None:
            row = conn.execute(
                """UPDATE workflows SET name = %s, description = %s, nodes = %s
                     WHERE project_id = %s AND id = %s RETURNING id""",
                (name, description, json.dumps(steps), project_id, workflow_id),
            ).fetchone()
            if not row:
                raise ValueError("Workflow not found in this project.")
            return int(row["id"])
        if conn.execute(
            "SELECT 1 FROM workflows WHERE project_id = %s AND name = %s", (project_id, name),
        ).fetchone():
            raise ValueError(f"A flow called '{name}' already exists.")
        row = conn.execute(
            """INSERT INTO workflows (project_id, name, description, color, pinned, status, nodes)
                 VALUES (%s, %s, %s, %s, %s, 'active', %s) RETURNING id""",
            (project_id, name, description, color, pinned, json.dumps(steps)),
        ).fetchone()
        return int(row["id"])
    return transaction(save)


def _delete(project_id: int, workflow_id: int):
    def delete(conn):
        row = conn.execute(
            "SELECT name FROM workflows WHERE project_id = %s AND id = %s FOR UPDATE",
            (project_id, workflow_id),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM workflow_runs WHERE project_id = %s AND workflow_id = %s",
                     (project_id, workflow_id))
        conn.execute("DELETE FROM workflows WHERE project_id = %s AND id = %s",
                     (project_id, workflow_id))
        return str(row["name"])
    return transaction(delete)


def _set_status(project_id: int, workflow_id: int, status: str):
    def update(conn):
        row = conn.execute(
            """UPDATE workflows SET status = %s WHERE project_id = %s AND id = %s
                 RETURNING name""", (status, project_id, workflow_id),
        ).fetchone()
        return str(row["name"]) if row else None
    return transaction(update)


def _set_pinned(project_id: int, workflow_id: int, pinned: bool) -> bool:
    return bool(transaction(lambda conn: conn.execute(
        """UPDATE workflows SET pinned = %s WHERE project_id = %s AND id = %s
             RETURNING id""", (pinned, project_id, workflow_id),
    ).fetchone()))


def ports() -> WorkflowPorts:
    return WorkflowPorts(
        create_run=_create_run, approve_waiting_run=_approve, save=_save,
        delete=_delete, set_status=_set_status, set_pinned=_set_pinned,
        start_run=lambda run_id, start_at: flowengine.start_run(run_id, start_at),
        audit=lambda verb, target: audit(verb, target),
    )

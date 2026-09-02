"""Postgres, audit, and executor adapters for workflow use cases."""

from __future__ import annotations

import json

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access
from mari_server.persistence.postgres.database import audit, jload, transaction
from mari_components.workflows import WorkflowPorts


_ROTATION_COLUMNS = {"facts": "facts_scanned_at", "decisions": "decisions_scanned_at"}


def select_documents(*, trigger_ids: list[int], tag: str, query: str,
                     limit: int, rotation: str,
                     source_ids: list[int] | None = None,
                     path_glob: str = "") -> list[dict]:
    column = _ROTATION_COLUMNS.get(rotation)
    order = (f"{column} NULLS FIRST, d.updated_src DESC NULLS LAST, d.id"
             if column else "d.updated_src DESC NULLS LAST, d.id DESC")
    project_id = access.require_current_access().project_id
    source_ids = [int(value) for value in (source_ids or []) if int(value) > 0]
    # User-facing path globs need only the familiar *, **, and ? forms. SQL
    # LIKE treats both star forms as %, which is correct for repository/folder
    # scoping because source_path always uses slash-separated relative paths.
    path_like = str(path_glob or "").strip().replace("**", "%").replace("*", "%").replace("?", "_")
    with db.connect() as conn:
        if trigger_ids:
            return conn.execute(
                """SELECT id, title FROM documents
                     WHERE project_id = %s AND id = ANY(%s)
                       AND (%s::int[] = '{}' OR source_id = ANY(%s))
                       AND (%s = '' OR source_path LIKE %s) ORDER BY id""",
                (project_id, trigger_ids, source_ids, source_ids, path_like, path_like),
            ).fetchall()
        if rotation == "facts":
            return conn.execute(
                """SELECT d.id, d.title FROM documents d
                    WHERE d.project_id = %s
                      AND (%s = '' OR d.source_path LIKE %s)
                      AND (%s = '' OR EXISTS (
                        SELECT 1 FROM tags t WHERE t.project_id = d.project_id
                          AND t.document_id = d.id AND t.tag = %s))
                      AND (%s::int[] = '{}' OR d.source_id = ANY(%s))
                      AND EXISTS (
                        SELECT 1 FROM chunks c
                         WHERE c.project_id = d.project_id AND c.document_id = d.id
                           AND NOT EXISTS (
                             SELECT 1 FROM fact_chunk_scans scanned
                              WHERE scanned.project_id = c.project_id AND scanned.chunk_id = c.id
                                AND scanned.content_hash = c.content_hash)
                           AND (%s = '' OR to_tsvector('english', c.content)
                                @@ plainto_tsquery('english', %s) OR c.content ILIKE %s))
                    ORDER BY d.facts_scanned_at NULLS FIRST,
                             d.updated_src DESC NULLS LAST, d.id
                    LIMIT %s""",
                (project_id, path_like, path_like, tag, tag, source_ids, source_ids,
                 query, query, f"%{query}%", limit),
            ).fetchall()
        needs_scan = (f" AND ({column} IS NULL OR d.updated_src > {column})" if column else "")
        return conn.execute(
            f"""SELECT d.id, d.title FROM documents d WHERE d.project_id = %s{needs_scan}
                   AND (%s = '' OR d.source_path LIKE %s)
                   AND (%s = '' OR d.search_vec @@ plainto_tsquery('english', %s)
                        OR d.title ILIKE %s)
                   AND (%s = '' OR EXISTS (
                     SELECT 1 FROM tags t WHERE t.project_id = d.project_id
                       AND t.document_id = d.id AND t.tag = %s))
                   AND (%s::int[] = '{{}}' OR d.source_id = ANY(%s))
                 ORDER BY {order} LIMIT %s""",
            (project_id, path_like, path_like, query, query, f"%{query}%", tag, tag,
             source_ids, source_ids, limit),
        ).fetchall()


def document(document_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE project_id = %s AND id = %s",
            (project_id, document_id),
        ).fetchone()


def save_suggested_changes(document_id: int,
                           changes: list[tuple[str, str, str]]) -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        for original, replacement, reason in changes:
            conn.execute(
                """INSERT INTO changes
                   (project_id, document_id, original, replacement, reason)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (document_id, original) DO NOTHING""",
                (project_id, document_id, original, replacement, reason),
            )
    return len(changes)


def tag_documents(document_ids: list[int], tag: str) -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        for document_id in document_ids:
            conn.execute(
                """INSERT INTO tags (project_id, document_id, tag)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (project_id, document_id, tag),
            )
    return len(document_ids)


def create_review_task(title: str, assignee: str, kind: str, kind_label: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO tasks
               (project_id, title, assignee, assignee_initials, assignee_tint, kind, kind_label)
               VALUES (%s, %s, %s, %s, 2, %s, %s)
               ON CONFLICT (project_id, title) DO NOTHING""",
            (project_id, title[:120], assignee,
             "".join(word[0] for word in assignee.split()[:2]).upper(), kind, kind_label),
        )


def list_workflows() -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT w.*, latest.number AS last_run_number,
                      latest.status AS last_run_status,
                      latest.started_at AS last_run_started
                 FROM workflows w
                 LEFT JOIN LATERAL (
                   SELECT number, status, started_at
                     FROM workflow_runs r
                    WHERE r.project_id = w.project_id AND r.workflow_id = w.id
                    ORDER BY r.number DESC LIMIT 1
                 ) latest ON true
                WHERE w.project_id = %s ORDER BY w.id""", (project_id,),
        ).fetchall()


def list_runs(workflow_id: int | None = None, limit: int = 10) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        if workflow_id is not None:
            return conn.execute(
                """SELECT r.*, w.name AS wf_name FROM workflow_runs r
                   JOIN workflows w ON w.project_id = r.project_id AND w.id = r.workflow_id
                  WHERE r.project_id = %s AND r.workflow_id = %s
                  ORDER BY r.number DESC LIMIT %s""", (project_id, workflow_id, limit),
            ).fetchall()
        return conn.execute(
            """SELECT r.*, w.name AS wf_name FROM workflow_runs r
               JOIN workflows w ON w.project_id = r.project_id AND w.id = r.workflow_id
              WHERE r.project_id = %s ORDER BY r.number DESC LIMIT %s""", (project_id, limit),
        ).fetchall()


def get_run(run_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT r.*, w.name AS wf_name FROM workflow_runs r
               JOIN workflows w ON w.project_id = r.project_id AND w.id = r.workflow_id
              WHERE r.project_id = %s AND r.id = %s""", (project_id, run_id),
        ).fetchone()


def latest_visible_run(workflow_id: int) -> dict | None:
    """Newest run this user has not dismissed from the Facts workspace."""
    context = access.require_current_access()
    with db.connect() as conn:
        return conn.execute(
            """WITH latest AS (
                 SELECT * FROM workflow_runs
                  WHERE project_id = %s AND workflow_id = %s
                  ORDER BY number DESC LIMIT 1
               )
               SELECT r.*, w.name AS wf_name FROM latest r
               JOIN workflows w ON w.project_id = r.project_id AND w.id = r.workflow_id
               LEFT JOIN workflow_run_dismissals d
                 ON d.project_id = r.project_id AND d.run_id = r.id AND d.user_id = %s
              WHERE d.run_id IS NULL""",
            (context.project_id, workflow_id, context.user_id),
        ).fetchone()


def dismiss_run(run_id: int) -> bool:
    context = access.require_current_access()
    if context.user_id <= 0:
        return False
    with db.connect() as conn, conn.transaction():
        return bool(conn.execute(
            """INSERT INTO workflow_run_dismissals (project_id, run_id, user_id)
               SELECT %s, id, %s FROM workflow_runs
                WHERE project_id = %s AND id = %s
               ON CONFLICT (project_id, run_id, user_id)
               DO UPDATE SET dismissed_at = now()
               RETURNING run_id""",
            (context.project_id, context.user_id, context.project_id, run_id),
        ).fetchone())


def _refuse_concurrent_run(conn, project_id: int, workflow_id: int) -> None:
    """Raise when this workflow already has a run in flight.

    The console disables Run now while a run executes, but a stale tab, the
    API, or the agent can still ask, and two concurrent runs of one workflow
    interleave their staged candidates. Callers must hold the workflow row
    FOR UPDATE first so the check and the insert serialize with delete."""
    running = conn.execute(
        """SELECT 1 FROM workflow_runs
            WHERE project_id = %s AND workflow_id = %s AND status = 'running'
            LIMIT 1""", (project_id, workflow_id)).fetchone()
    if running:
        raise ValueError("This workflow already has a run in progress.")


def create_run(workflow_id: int) -> dict:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute("SELECT 1 FROM workflows WHERE project_id = %s AND id = %s FOR UPDATE",
                     (project_id, workflow_id))
        _refuse_concurrent_run(conn, project_id, workflow_id)
        return conn.execute("""INSERT INTO workflow_runs
          (project_id, workflow_id, number, status, started_label, duration, progress, stats, rows_data,
           heartbeat_at)
          VALUES (%s, %s, nextval('workflow_run_number_seq'), 'running',
          to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, '{}', '[]', now()) RETURNING id, number""",
          (project_id, workflow_id)).fetchone()


def set_trigger(workflow_id: int, trigger: dict) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "SELECT nodes FROM workflows WHERE project_id = %s AND id = %s FOR UPDATE",
            (project_id, workflow_id),
        ).fetchone()
        if not row:
            return False
        nodes = jload(row["nodes"]) or []
        if (trigger.get("on") or "") in {"", "schedule"}:
            every = int(trigger.get("every_minutes") or 0)
            if every == 0:
                label, detail = "Manual", "Started manually"
            elif every == 60:
                label, detail = "Every hour", "Scheduled · hourly"
            elif every == 1440:
                label, detail = "Every day", "Scheduled · daily"
            elif every == 10080:
                label, detail = "Every week", "Scheduled · weekly"
            elif every % 60 == 0:
                hours = every // 60
                label = f"Every {hours} hours"
                detail = f"Scheduled · every {hours} hours"
            else:
                label = f"Every {every} min"
                detail = f"Scheduled · every {every} min"
            for node in nodes:
                if node.get("kind") == "trigger":
                    node["label"] = label
                    node["config"] = {**(node.get("config") or {}), "label": detail}
                    break
        conn.execute(
            "UPDATE workflows SET trigger = %s, nodes = %s WHERE project_id = %s AND id = %s",
            (json.dumps(trigger), json.dumps(nodes), project_id, workflow_id),
        )
        return True


def update_metadata(workflow_id: int, name: str, description: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        return bool(conn.execute(
            """UPDATE workflows SET name = %s, description = %s
                 WHERE project_id = %s AND id = %s RETURNING id""",
            (name, description, project_id, workflow_id),
        ).fetchone())


def create_notification(recipient: str, text: str, detail: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO notifications
               (project_id, user_name, kind, text, detail, at_label, read)
               VALUES (%s, %s, 'info', %s, %s, 'just now', false)
               ON CONFLICT (user_name, text) DO NOTHING""",
            (project_id, recipient, text[:180], detail[:200]),
        )


def documents(document_ids: list[int]) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT id, title, body, snippet, updated_src FROM documents
                 WHERE project_id = %s AND id = ANY(%s)""",
            (project_id, document_ids),
        ).fetchall()


def source_name(source_id: int) -> str | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            "SELECT display_name FROM sources WHERE project_id = %s AND id = %s",
            (project_id, source_id),
        ).fetchone()
    return str(row["display_name"]) if row else None


# The only statuses a live runner may write over. A row the stale sweep
# already flipped to 'failed' (or one that finished) is never rewritten: the
# runner's later persist used to put status='running' back unconditionally,
# so a run swept while it sat in the worker queue resurrected next to the
# replacement run the scheduler had already started.
_LIVE_RUN_STATUSES = ("running", "waiting")


def save_run_progress(run_id: int, *, rows: list[dict], status: str,
                      progress: int, stats: dict, duration: str) -> bool:
    """Persist the run's rows and stamp its heartbeat. Returns False when the
    row is no longer live (swept by fail_stale_runs, or already terminal),
    in which case nothing was written and the runner must stop.

    Every persist is also the run's heartbeat: a 'running' row whose
    heartbeat stops moving has no process behind it (see fail_stale_runs)."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """UPDATE workflow_runs SET rows_data = %s, status = %s, progress = %s,
                   stats = %s, duration = %s, heartbeat_at = now()
                 WHERE project_id = %s AND id = %s AND status IN ('running', 'waiting')
                 RETURNING id""",
            (json.dumps(rows), status, progress, json.dumps(stats), duration,
             project_id, run_id),
        ).fetchone()
    return row is not None


def touch_run_heartbeat(run_id: int) -> bool:
    """Prove the run is still alive without rewriting its rows. The runner's
    heartbeat ticker calls this between persists, so a long step that never
    narrates progress (a big connector sync, an LLM pass) keeps its lease;
    the queue ticker calls it for runs still waiting on a pool worker.
    Returns False when the row is no longer live, so a swept run is never
    revived by a beat."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """UPDATE workflow_runs SET heartbeat_at = now()
                 WHERE project_id = %s AND id = %s AND status IN ('running', 'waiting')
                 RETURNING id""",
            (project_id, run_id),
        ).fetchone()
    return row is not None


def full_sync_due(source_id: int, every_hours: float) -> bool:
    """Whether this source is owed an authoritative full reconcile: never had
    one, or the last one is older than `every_hours`. Compared in the database
    so the clock that wrote last_full_sync_at is the clock that reads it."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            """SELECT (last_full_sync_at IS NULL
                       OR last_full_sync_at < now() - make_interval(secs => %s)) AS due
                 FROM sources WHERE project_id = %s AND id = %s""",
            (float(every_hours) * 3600.0, project_id, source_id),
        ).fetchone()
    return bool(row and row["due"])


def record_full_sync(source_id: int) -> None:
    """Called only after a full sync returned without error. A dedicated
    column rather than sources.config, which the sync worker owns and
    rewrites wholesale."""
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            "UPDATE sources SET last_full_sync_at = now() WHERE project_id = %s AND id = %s",
            (project_id, source_id),
        )


def load_run(run_id: int) -> tuple[dict, dict] | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        run = conn.execute(
            "SELECT * FROM workflow_runs WHERE project_id = %s AND id = %s",
            (project_id, run_id),
        ).fetchone()
        if not run:
            return None
        workflow = conn.execute(
            "SELECT * FROM workflows WHERE project_id = %s AND id = %s",
            (project_id, run["workflow_id"]),
        ).fetchone()
    return (run, workflow) if workflow else None


def fail_running_run(run_id: int, note: str) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE workflow_runs SET status = 'failed', progress = 100,
                 stats = coalesce(stats, '{}'::jsonb) ||
                         jsonb_build_object('note', CAST(%s AS text))
               WHERE project_id = %s AND id = %s AND status = 'running'""",
            (note[:200], project_id, run_id),
        )


def trigger_inputs(document_ids: list[int], change: str) -> tuple[list[dict], list[dict], dict[int, set]]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        docs = conn.execute(
            """SELECT id, title, source_id, source_path FROM documents
                 WHERE project_id = %s AND id = ANY(%s)""",
            (project_id, document_ids),
        ).fetchall()
        workflows = conn.execute(
            """SELECT id, project_id, name, trigger FROM workflows
                 WHERE project_id = %s AND status = 'active'
                   AND trigger->>'on' = %s ORDER BY id""",
            (project_id, change),
        ).fetchall()
        tags: dict[int, set] = {}
        for row in conn.execute(
            """SELECT document_id, tag FROM tags
                 WHERE project_id = %s AND document_id = ANY(%s)""",
            (project_id, [doc["id"] for doc in docs]),
        ).fetchall():
            tags.setdefault(row["document_id"], set()).add(row["tag"])
    return docs, workflows, tags


def _lock_workflow(conn, project_id: int | None, workflow_id: int) -> None:
    """Hold the workflow row so this insert serializes with every other run
    creation and with delete. Scheduled and triggered runs used to skip this,
    so a manual Run now racing the scheduler tick produced two interleaved
    runs of one workflow; they now take the same lock and the same
    concurrency check as create_run."""
    row = conn.execute(
        "SELECT 1 FROM workflows WHERE project_id IS NOT DISTINCT FROM %s AND id = %s FOR UPDATE",
        (project_id, workflow_id),
    ).fetchone()
    if not row:
        raise ValueError("This workflow no longer exists.")
    _refuse_concurrent_run(conn, project_id, workflow_id)


def create_triggered_run(workflow: dict, document_ids: list[int],
                         trigger: dict, note: str) -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        _lock_workflow(conn, project_id, workflow["id"])
        row = conn.execute(
            """INSERT INTO workflow_runs
               (project_id, workflow_id, number, status, started_label, duration,
                progress, stats, rows_data, triggered_by, heartbeat_at)
               VALUES (%s, %s, nextval('workflow_run_number_seq'), 'running',
                       to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]', %s, now())
               RETURNING id, number""",
            (project_id, workflow["id"],
             json.dumps({"ctx": {"trigger_doc_ids": document_ids, "trigger": trigger},
                         "trigger": trigger}), note),
        ).fetchone()
        conn.execute(
            """INSERT INTO events (project_id, actor, verb, target)
               VALUES (%s, %s, %s, %s)""",
            (project_id, "Flow trigger",
             f"auto-started run #{row['number']} ({note[:80]})", workflow["name"]),
        )
    return int(row["id"])


def reconcile_stale_runs(process_started_at: float) -> int:
    with db.connect() as conn, conn.transaction():
        rows = conn.execute(
            """UPDATE workflow_runs
               SET status = 'failed',
                   stats = coalesce(stats, '{}'::jsonb) ||
                           '{"note": "interrupted by restart"}'::jsonb
               WHERE status = 'running' AND started_at < to_timestamp(%s)
               RETURNING id""", (process_started_at,),
        ).fetchall()
        if rows:
            conn.execute(
                """INSERT INTO events (actor, verb, target)
                   VALUES (%s, %s, %s)""",
                ("Flow scheduler",
                 f"marked {len(rows)} stale run(s) failed (interrupted by restart)",
                 "startup reconciliation"),
            )
    return len(rows)


def scheduled_workflows() -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            """SELECT id, project_id, name, trigger FROM workflows
               WHERE project_id IS NOT NULL AND status = 'active'
                 AND trigger->>'on' = 'schedule' ORDER BY id""",
        ).fetchall()


def latest_run(workflow_id: int, every_minutes: int) -> dict | None:
    # number DESC: the same ordering list_workflows derives last-run from, so
    # the scheduler and the console agree on which run is the newest.
    with db.connect() as conn:
        return conn.execute(
            """SELECT status, (now() - started_at) >= make_interval(mins => %s) AS due
               FROM workflow_runs WHERE workflow_id = %s ORDER BY number DESC LIMIT 1""",
            (every_minutes, workflow_id),
        ).fetchone()


def fail_stale_runs(stale_after_seconds: float) -> int:
    """Fail every 'running' run whose heartbeat is older than the threshold.

    The runner heartbeats on every persist, so a run that stopped touching
    its row has no process behind it: _persist raised on a DB blip and the
    fallback fail_running_run was swallowed, or the worker was killed. Left
    alone it blocks that workflow's schedule until the next restart. The
    scheduler calls this once per pass, before it asks which runs are live.
    Returns how many were flipped.

    A run waiting for a pool worker is alive too: the runtime's queue ticker
    beats for it until a worker starts, and approveRun restamps the beat
    when it resumes a waiting run, so neither reads as lost here."""
    with db.connect() as conn, conn.transaction():
        rows = conn.execute(
            """UPDATE workflow_runs
               SET status = 'failed', progress = 100,
                   stats = coalesce(stats, '{}'::jsonb) ||
                           jsonb_build_object('note', CAST(%s AS text))
               WHERE status = 'running'
                 AND heartbeat_at < now() - make_interval(secs => %s)
               RETURNING id""",
            (f"no heartbeat for {int(stale_after_seconds // 60)} min; marked failed",
             float(stale_after_seconds)),
        ).fetchall()
        if rows:
            conn.execute(
                "INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                ("Flow scheduler",
                 f"marked {len(rows)} stale run(s) failed (lost heartbeat)",
                 "scheduler reconciliation"),
            )
    return len(rows)


def create_scheduled_run(workflow: dict, trigger: dict, label: str) -> int:
    with db.connect() as conn, conn.transaction():
        _lock_workflow(conn, workflow.get("project_id"), workflow["id"])
        row = conn.execute(
            """INSERT INTO workflow_runs
               (project_id, workflow_id, number, status, started_label, duration,
                progress, stats, rows_data, triggered_by, heartbeat_at)
               VALUES (%s, %s, nextval('workflow_run_number_seq'), 'running',
                       to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]', %s, now())
               RETURNING id, number""",
            (workflow.get("project_id"), workflow["id"],
             json.dumps({"ctx": {"trigger": trigger}, "trigger": trigger}), label),
        ).fetchone()
        conn.execute(
            "INSERT INTO events (project_id, actor, verb, target) VALUES (%s, %s, %s, %s)",
            (workflow.get("project_id"), "Flow scheduler",
             f"auto-started run #{row['number']} ({label})", workflow["name"]),
        )
    return int(row["id"])


def run_project(run_id: int) -> dict | None:
    """Resolve the immutable project identity a background run must carry."""
    with db.connect() as conn:
        return conn.execute(
            """SELECT p.id, p.slug, p.name FROM workflow_runs r
               JOIN projects p ON p.id = r.project_id
               WHERE r.id = %s AND p.status = 'active'""", (run_id,),
        ).fetchone()


def fail_unroutable_run(run_id: int, note: str) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE workflow_runs SET status = 'failed', progress = 100,
                      stats = coalesce(stats, '{}'::jsonb) ||
                              jsonb_build_object('note', CAST(%s AS text))
                 WHERE id = %s AND status = 'running'""", (note[:200], run_id),
        )


def active_projects() -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT id, slug, name FROM projects WHERE status = 'active' ORDER BY id",
        ).fetchall()


def find_by_step(step_kind: str, *, project_scoped: bool = True,
                 config: dict | None = None) -> dict | None:
    project_id = access.require_current_access().project_id if project_scoped else None
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, name, description, trigger, nodes FROM workflows WHERE project_id = %s"
            if project_scoped else
            "SELECT id, name, description, trigger, nodes FROM workflows",
            (project_id,) if project_scoped else (),
        ).fetchall()
    for row in rows:
        nodes = row["nodes"] if isinstance(row["nodes"], list) else json.loads(row["nodes"] or "[]")
        if any(
            isinstance(step, dict) and step.get("kind") == step_kind
            and all((step.get("config") or {}).get(key) == value
                    for key, value in (config or {}).items())
            for step in nodes
        ):
            return {**row, "nodes": nodes}
    return None


def update_nodes(workflow_id: int, nodes: list[dict]) -> None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn, conn.transaction():
        conn.execute(
            "UPDATE workflows SET nodes = %s WHERE project_id = %s AND id = %s",
            (json.dumps(nodes), project_id, workflow_id),
        )


def workflow_nodes(workflow_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            "SELECT nodes FROM workflows WHERE project_id = %s AND id = %s",
            (project_id, workflow_id),
        ).fetchone()
    if not row:
        raise ValueError("Fact scan workflow not found")
    return row["nodes"] if isinstance(row["nodes"], list) else json.loads(row["nodes"] or "[]")


def create_default_workflow(*, name: str, description: str, color: str,
                            status: str, nodes: list[dict], trigger: dict,
                            project_scoped: bool = True) -> int:
    project_id = access.require_current_access().project_id if project_scoped else None
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            """INSERT INTO workflows
               (project_id, name, description, color, pinned, status, nodes, trigger)
               VALUES (%s, %s, %s, %s, false, %s, %s, %s) RETURNING id""",
            (project_id, name, description, color, status,
             json.dumps(nodes), json.dumps(trigger)),
        ).fetchone()
        conn.execute(
            "INSERT INTO events (project_id, actor, verb, target) VALUES (%s, %s, %s, %s)",
            (project_id, "Flow scheduler", "created flow", name),
        )
    return int(row["id"])


def setting(key: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    value = (row or {}).get("value") or {}
    return json.loads(value or "{}") if isinstance(value, str) else value


def connector_sources() -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            """SELECT s.id, s.display_name, s.config, p.id AS project_id,
                      p.slug AS project_slug, p.name AS project_name
                 FROM sources s JOIN projects p ON p.id = s.project_id
                WHERE s.kind = 'connector' AND p.status = 'active'""",
        ).fetchall()


def quarantine_orphan_sync_workflows() -> int:
    """Archive legacy sync jobs that cannot belong to a live source.

    Older bootstrap code wrote connector workflows without a project and, on
    each restart, compared a source only with the first sync workflow it found.
    Keep their run history, but make the rows non-runnable and invisible to the
    scheduler manager. Project-scoped jobs whose source was removed receive the
    same treatment.
    """
    with db.connect() as conn, conn.transaction():
        rows = conn.execute(
            """UPDATE workflows w
                  SET status = 'archived', trigger = '{"on": ""}'::jsonb
                WHERE status <> 'archived'
                  AND EXISTS (
                    SELECT 1 FROM jsonb_array_elements(w.nodes) node
                     WHERE node->>'kind' = 'sync_source'
                       AND (
                         w.project_id IS NULL OR NOT EXISTS (
                           SELECT 1 FROM sources s
                            WHERE s.project_id = w.project_id
                              AND s.id = CASE
                                WHEN (node->'config'->>'source_id') ~ '^[0-9]+$'
                                THEN (node->'config'->>'source_id')::int
                                ELSE -1 END
                         )
                       )
                  )
              RETURNING id""",
        ).fetchall()
    return len(rows)


def digest_topic_count() -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM digest_topics WHERE project_id = %s",
            (project_id,),
        ).fetchone()
    return int(row["n"])


def _create_run(project_id: int, workflow_id: int, dry_run: bool):
    def create(conn):
        workflow = conn.execute(
            "SELECT name FROM workflows WHERE project_id = %s AND id = %s FOR UPDATE",
            (project_id, workflow_id),
        ).fetchone()
        if not workflow:
            return None
        _refuse_concurrent_run(conn, project_id, workflow_id)
        row = conn.execute(
            """INSERT INTO workflow_runs
                 (project_id, workflow_id, number, status, started_label, duration, progress, stats,
                  rows_data, heartbeat_at)
               VALUES (%s, %s, nextval('workflow_run_number_seq'), 'running',
                       to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]', now())
               RETURNING id, number""",
            (project_id, workflow_id,
             json.dumps({"ctx": {"dry_run": True}, "dry_run": True} if dry_run else {})),
        ).fetchone()
        return (int(row["id"]), int(row["number"]), str(workflow["name"]))
    return transaction(create)


def _approve(project_id: int, run_id: int, actor: str):
    def approve(conn):
        run = conn.execute(
            """SELECT * FROM workflow_runs WHERE project_id = %s AND id = %s
                 FOR UPDATE""", (project_id, run_id),
        ).fetchone()
        if not run or run["status"] != "waiting":
            return None
        pending = conn.execute(
            """SELECT count(*) AS n FROM fact_extraction_candidates
                WHERE project_id = %s AND run_id = %s AND review_status = 'pending'""",
            (project_id, run_id),
        ).fetchone()
        if pending and int(pending["n"]):
            return None
        stats, rows = jload(run["stats"]) or {}, jload(run["rows_data"]) or []
        paused_at = int(stats.get("paused_at", 0))
        if paused_at < len(rows):
            rows[paused_at].update(status="passed", detail=f"approved by {actor}")
        # A waiting row's heartbeat is whatever the last persist stamped
        # before it paused, hours or days ago. Restarting the lease here,
        # not when a pool worker finally picks the run up, keeps the stale
        # sweep from failing a run that is merely queued behind a busy pool.
        conn.execute(
            """UPDATE workflow_runs SET rows_data = %s, status = 'running',
                   heartbeat_at = now()
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
        # The GraphQL guard reads last_run_status before this transaction, so
        # a run starting in between would be deleted mid-flight along with its
        # history. Re-check under the row lock, where run creation serializes.
        running = conn.execute(
            """SELECT 1 FROM workflow_runs
                WHERE project_id = %s AND workflow_id = %s AND status = 'running'
                LIMIT 1""", (project_id, workflow_id)).fetchone()
        if running:
            raise ValueError("This task is still running. Wait for it to finish, then remove it.")
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


def ports(start_run) -> WorkflowPorts:
    return WorkflowPorts(
        create_run=_create_run, approve_waiting_run=_approve, save=_save,
        delete=_delete, set_status=_set_status, set_pinned=_set_pinned,
        start_run=start_run,
        audit=lambda verb, target: audit(verb, target),
    )

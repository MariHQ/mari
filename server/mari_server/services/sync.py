"""Connector sync coordination and document indexing primitives."""

from __future__ import annotations

import threading
import time

from mari_server.services import workflow_runtime as flowengine
from mari_server.domain import access
from mari_server import db as postgres

PROCESS_START_TS = time.time()  # for startup reconciliation — never touch newer rows


def connection():
    return postgres.connect()


# ————————————————— status registry —————————————————

_LOCK = threading.Lock()
_STATUS: dict[int, dict] = {}
_IDLE = {"state": "idle", "phase": "", "done": 0, "total": 0, "error": ""}


def update_status(source_id: int, **kw) -> None:
    with _LOCK:
        _STATUS.setdefault(source_id, dict(_IDLE)).update(kw)


def status(source_id: int) -> dict:
    with _LOCK:
        return dict(_STATUS.get(source_id) or _IDLE)


_RUNNING: set[int] = set()


def _worker_for(source_id: int):
    """Dispatch connector sources through the shared component-backed worker."""
    with connection() as conn:
        row = conn.execute("SELECT kind FROM sources WHERE id = %s AND project_id = %s",
                           (source_id, access.require_current_access().project_id)).fetchone()
    if not row or row.get("kind") != "connector":
        raise RuntimeError("source is not a connector")
    from mari_server.services import connector_sync  # late: runtime imports ingest
    return connector_sync.sync_source


def _run_guarded(source_id: int, full: bool, project_access=None) -> dict:
    """Dispatch + run one sync with the _RUNNING slot released no matter how
    the worker exits — early returns, dispatch errors, and crashes included.
    This is the ONLY place the slot is released; workers never touch it."""
    try:
        if project_access is None:
            project_access = access.require_current_access()
        with access.use_access(project_access):
            return _worker_for(source_id)(source_id, full)
    finally:
        with _LOCK:
            _RUNNING.discard(source_id)


def start_sync(source_id: int, full: bool = False) -> bool:
    """Kick off a background sync; returns False if one is already running."""
    project_access = access.require_current_access()
    with _LOCK:
        if source_id in _RUNNING:
            return False
        _RUNNING.add(source_id)
    # mark running synchronously so a syncStatus poll right after the mutation
    # never sees a stale 'idle'
    update_status(source_id, state="running", phase="listing", done=0, total=0, error="")
    threading.Thread(target=_run_guarded, args=(source_id, full, project_access), daemon=True).start()
    return True


def run_sync(source_id: int, full: bool = False) -> dict | None:
    """Synchronous sync for flow sync_source steps: runs the worker on the
    caller's thread and returns its honest stats dict ('error' key on failure).
    Returns None if a sync for this source is already running."""
    with _LOCK:
        if source_id in _RUNNING:
            return None
        _RUNNING.add(source_id)
    update_status(source_id, state="running", phase="listing", done=0, total=0, error="")
    return _run_guarded(source_id, full)


# ————————————————— startup wiring —————————————————
#
# Every connector gets a schedule-triggered "Sync <source>" flow with a
# sync_source step, visible and editable in the Flows UI. The public name is
# kept because app.py's startup path calls start_poller().

_POLLER = {"started": False}


def reconcile_stale_checkpoints() -> int:
    """Startup reconciliation: a checkpoint left 'running' by an unclean
    shutdown would show a phantom in-flight sync forever. Flip rows from
    BEFORE this process started to 'paused' (the error-path status) with a
    truthful sync_events note; rows this process wrote are never touched.
    Returns how many were flipped."""
    with connection() as conn:
        rows = conn.execute(
            """UPDATE ingest_checkpoints SET status = 'paused', updated_at = now()
               WHERE status = 'running' AND updated_at < to_timestamp(%s)
               RETURNING provider, item""", (PROCESS_START_TS,)).fetchall()
        for r in rows:
            conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                         (r["provider"], f"sync interrupted: {r['item']}",
                          "checkpoint was still 'running' at startup — interrupted by restart, marked paused"))
        conn.commit()
    return len(rows)


def start_poller() -> None:
    """Startup wiring: seed the scheduled sync/digest flows and start the flow
    scheduler. The schema it depends on is already applied by db.ensure_schema()
    (init.sql) earlier in startup. Guarded so uvicorn --reload / repeat imports
    don't double-start."""
    if _POLLER["started"]:
        return
    _POLLER["started"] = True

    try:
        reconcile_stale_checkpoints()
    except Exception:  # noqa: BLE001 — never block startup on reconciliation
        pass
    try:
        flowengine.seed_scheduled_flows()
    except Exception:  # noqa: BLE001 — never block startup on seeding
        pass
    flowengine.start_scheduler()


def stop_poller() -> None:
    """Release startup-owned background services during ASGI shutdown."""
    flowengine.stop_scheduler()
    _POLLER["started"] = False

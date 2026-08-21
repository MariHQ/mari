"""Connector sync coordination and document indexing primitives."""

from __future__ import annotations

import logging
import threading
import time
from functools import partial

from mari_server.automations import runtime as flowengine
from mari_server.identity import context as access
from mari_server.persistence.postgres import sources as source_store

PROCESS_START_TS = time.time()  # for startup reconciliation — never touch newer rows


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
log = logging.getLogger(__name__)


def _worker_for(source_id: int):
    """Dispatch connector sources through the shared component-backed worker."""
    if source_store.source_kind(source_id) != "connector":
        raise RuntimeError("source is not a connector")
    from mari_server.persistence.postgres import connector_sync  # late: runtime imports ingest
    from mari_server.search.service import invalidate_search
    return partial(
        connector_sync.sync_source,
        update_status=update_status,
        fire_document_triggers=flowengine.fire_document_triggers,
        invalidate_search=invalidate_search,
    )


def _run_guarded(source_id: int, full: bool, project_access=None) -> dict:
    """Dispatch + run one sync with the _RUNNING slot released no matter how
    the worker exits — early returns, dispatch errors, and crashes included.
    This is the ONLY place the slot is released; workers never touch it."""
    try:
        if project_access is None:
            project_access = access.require_current_access()
        with access.use_access(project_access):
            return _worker_for(source_id)(source_id, full)
    except Exception as error:  # the status registry must never strand a run as active
        authored = (type(error).__module__ or "").startswith("mari_components") or isinstance(
            error, (ValueError, RuntimeError, ConnectionError, TimeoutError),
        )
        message = str(error) if authored and str(error) else f"Connector crashed ({type(error).__name__})"
        update_status(source_id, state="error", phase="", error=message)
        log.exception("source sync %s crashed", source_id)
        return {"error": message}
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
    return source_store.reconcile_stale_checkpoints(PROCESS_START_TS)


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

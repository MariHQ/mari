"""Connector sync coordination and document indexing primitives."""

from __future__ import annotations

import hashlib
import json
import statistics
import threading
import time

from mari_server.infrastructure import workflow_runtime as flowengine
from mari_server.infrastructure import models as llm
from mari_server.infrastructure import retrieval
from mari_server.domain import access
from mari_server.application import documents as document_application
from mari_server.domain.documents import DocumentVersion
from mari_server.infrastructure import document_repository
from mari_server.infrastructure import postgres

PROCESS_START_TS = time.time()  # for startup reconciliation — never touch newer rows


def _conn():
    return postgres.connect()


# ————————————————— status registry —————————————————

_LOCK = threading.Lock()
_STATUS: dict[int, dict] = {}
_IDLE = {"state": "idle", "phase": "", "done": 0, "total": 0, "error": ""}


def _set(source_id: int, **kw) -> None:
    with _LOCK:
        _STATUS.setdefault(source_id, dict(_IDLE)).update(kw)


def status(source_id: int) -> dict:
    with _LOCK:
        return dict(_STATUS.get(source_id) or _IDLE)


# ————————————————— chunking —————————————————


def _chunk_settings() -> tuple[int, int]:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'chunking'").fetchone()
    cfg = (row["value"] if row else {}) or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    d = cfg.get("default", {})
    return int(d.get("max_tokens", 512)), int(d.get("overlap", 64))


def chunk_text(text: str, max_tokens: int, overlap: int) -> list[str]:
    """Word-based approximation of token chunking (~0.75 words/token)."""
    words = text.split()
    size = max(int(max_tokens * 0.75), 32)
    step = max(size - int(overlap * 0.75), size // 2)
    if len(words) <= size:
        return [" ".join(words)] if words else []
    return [" ".join(words[i:i + size]) for i in range(0, len(words), step) if words[i:i + size]]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("# ").strip() or fallback
    return fallback


# ————————————————— document + chunk upserts —————————————————


def _upsert_document(conn, source_id: int, external_id: str, title: str, body: str,
                     source_path: str, kind: str, content_hash: str, author: str,
                     source: str = "github", initials: str = "GH",
                     acl_visibility: str = "project",
                     acl_principals: tuple[str, ...] = ()) -> tuple[int, bool]:
    """Upsert one document. Returns (doc_id, inserted) — inserted is True for a
    brand-new row, False for an update (xmax = 0 only on fresh inserts).
    `source`/`initials` default to github; connect_sync passes the provider key."""
    project_id = access.require_current_access().project_id
    version = DocumentVersion(
        project_id=project_id, source_id=str(source_id), external_id=external_id,
        revision=content_hash, title=title, body=body, source_url=source_path,
        acl={"visibility": acl_visibility, "principals": list(acl_principals)},
        reason="connector ingestion", actor=author or "connector",
    )
    doc_id, inserted = document_application.upsert(
        version,
        document_application.ProjectionFields(
            source=source, kind=kind, author=author, author_initials=initials,
        ),
        ports=document_repository.ports(conn),
    )
    conn.commit()
    # Import lazily: queries imports ingest for status resolvers.
    from mari_server.infrastructure.search import invalidate_search
    invalidate_search(project_id)
    return doc_id, inserted


def _sync_chunks(conn, doc_id: int, title: str, body: str,
                 max_tokens: int, overlap: int) -> tuple[int, int]:
    """Chunk + hash + embed-only-changed. Returns (chunks, newly_embedded)."""
    pieces = chunk_text(f"{title}\n\n{body}", max_tokens, overlap)
    project_id = access.require_current_access().project_id
    existing = {r["idx"]: r["content_hash"] for r in conn.execute(
        "SELECT idx, content_hash FROM chunks WHERE project_id = %s AND document_id = %s",
        (project_id, doc_id)).fetchall()}
    embedded = 0
    for idx, piece in enumerate(pieces):
        h = _sha(piece)
        if existing.get(idx) == h:
            continue
        vec = llm.embed(piece)
        if vec:
            embedded += 1
        conn.execute("""
            INSERT INTO chunks (project_id, document_id, idx, content, content_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (document_id, idx) DO UPDATE SET
              content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
              embedding = EXCLUDED.embedding""",
            (project_id, doc_id, idx, piece, h, str(vec) if vec else None))
    conn.execute("DELETE FROM chunks WHERE project_id = %s AND document_id = %s AND idx >= %s",
                 (project_id, doc_id, len(pieces)))
    # doc-level embedding = mean of chunk embeddings (keeps existing doc search working)
    vecs = [r["embedding"] for r in conn.execute(
        """SELECT embedding::text AS embedding FROM chunks
           WHERE project_id = %s AND document_id = %s AND embedding IS NOT NULL""",
        (project_id, doc_id)).fetchall()]
    if vecs:
        parsed = [json.loads(v) for v in vecs]
        mean = [statistics.fmean(col) for col in zip(*parsed)]
        conn.execute("""UPDATE documents SET embedding = %s::vector
                        WHERE id = %s AND project_id = %s""", (str(mean), doc_id, project_id))
    conn.commit()
    # Chunk vectors are derived and intentionally live outside canonical
    # storage. A burst of changed documents produces one atomic MUVERA /
    # PolarQuant snapshot instead of rewriting an index per document.
    if embedded:
        retrieval.schedule_rebuild()
    return len(pieces), embedded


def _delete_documents(conn, doc_ids: list[int]) -> None:
    if not doc_ids:
        return
    document_application.delete(
        access.require_current_access().project_id, doc_ids,
        reason="provider tombstone", actor="connector",
        ports=document_repository.ports(conn),
    )
    conn.commit()
    from mari_server.infrastructure.search import invalidate_search
    invalidate_search(access.require_current_access().project_id)


_RUNNING: set[int] = set()


def _worker_for(source_id: int):
    """Dispatch connector sources through the shared component-backed worker."""
    with _conn() as conn:
        row = conn.execute("SELECT kind FROM sources WHERE id = %s AND project_id = %s",
                           (source_id, access.require_current_access().project_id)).fetchone()
    if not row or row.get("kind") != "connector":
        raise RuntimeError("source is not a connector")
    from mari_server.infrastructure import connector_runtime as connect_sync  # late: runtime imports ingest
    return connect_sync._sync_worker


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
    _set(source_id, state="running", phase="listing", done=0, total=0, error="")
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
    _set(source_id, state="running", phase="listing", done=0, total=0, error="")
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
    with _conn() as conn:
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

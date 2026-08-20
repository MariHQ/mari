"""Google Drive Changes push notifications backed by the durable event inbox."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from mari_server.identity import access
from mari_server.identity import routes as auth
from mari_server.persistence.postgres import document_index
from mari_server import settings as config
from mari_server.sources import sync as ingest
from mari_server.providers import connectors as component_connectors
from mari_server.persistence.postgres.event_inbox import DEFAULT_INBOX
from mari_server.persistence.postgres import provider_events as event_store
from mari_server.persistence.postgres import documents as document_repository
from mari_server.search.service import invalidate_search
from mari_components import PollRequest
from mari_components.connectors import (
    GoogleDriveConfig, connector_definition, start_google_drive_watch,
)
from mari_components.connectors.events import gdrive_change_hint
from mari_components.errors import IncompleteSnapshot


router = APIRouter()
_WATCH_DAYS = 6
_RENEW_BEFORE = dt.timedelta(hours=24)
_RENEW_INTERVAL_SECONDS = 3600
_renew_stop = threading.Event()
_renew_thread: threading.Thread | None = None
log = logging.getLogger("mari.gdrive_events")


class DriveWatchIn(BaseModel):
    source_id: int


def _json(value):
    return value if isinstance(value, dict) else json.loads(value or "{}")


def _source(source_id: int, project_id: int) -> dict | None:
    return event_store.source(source_id, project_id, "connector", "gdrive")


@router.post("/connectors/google-drive/watch")
def create_watch(
    body: DriveWatchIn,
    current: access.AccessContext = Depends(auth.require_capability("source.manage")),
) -> dict:
    project_id = current.project_id
    source = _source(body.source_id, project_id)
    if not source:
        raise HTTPException(404, "Google Drive source not found.")
    source_config = _json(source["config"])
    cursor = str(source_config.get("cursor") or "")
    if not cursor.startswith("changes:") or not cursor[8:]:
        raise HTTPException(409, "Complete the source's initial poll before enabling Drive events.")
    base = str(config.get("auth", "oauth_redirect_base") or "").rstrip("/")
    if not base.startswith("https://"):
        raise HTTPException(409, "Google Drive push notifications require a configured HTTPS public base URL.")

    channel_id = str(uuid.uuid4())
    channel_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(channel_token.encode()).hexdigest()
    page_token = cursor[8:]
    expiration = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=_WATCH_DAYS)
    event_store.create_drive_watch(project_id, source["id"], channel_id, token_hash, page_token, expiration)
    try:
        watched = start_google_drive_watch(
            GoogleDriveConfig(
                str(source_config.get("access_token") or ""),
                str(source_config.get("folder_id") or ""),
            ),
            page_token,
            f"{base}/webhooks/google-drive",
            channel_id,
            channel_token,
            expiration_ms=round(expiration.timestamp() * 1000),
            http=component_connectors.http_transport,
        )
    except Exception as exc:
        error = str(exc)
        event_store.update_drive_watch(channel_id, status="error", last_error=error[:1000])
        raise HTTPException(502, error) from exc
    resource_id = watched.resource_id
    if watched.expiration_ms is not None:
        expiration = dt.datetime.fromtimestamp(watched.expiration_ms / 1000, tz=dt.timezone.utc)
    event_store.activate_drive_watch(channel_id, source["id"], resource_id, expiration)
    return {"ok": True, "channelId": channel_id, "sourceId": source["id"],
            "expiration": expiration.isoformat()}


@router.post("/webhooks/google-drive")
async def gdrive_webhook(request: Request):
    channel_id = request.headers.get("X-Goog-Channel-ID", "").strip()
    channel_token = request.headers.get("X-Goog-Channel-Token", "").strip()
    resource_id = request.headers.get("X-Goog-Resource-ID", "").strip()
    resource_state = request.headers.get("X-Goog-Resource-State", "").strip().lower()
    raw_number = request.headers.get("X-Goog-Message-Number", "").strip()
    if not all((channel_id, channel_token, resource_id, resource_state, raw_number)):
        return Response(status_code=400, content="missing Google Drive notification headers")
    if len(channel_id) > 64 or len(channel_token) > 256 or len(resource_id) > 1000:
        return Response(status_code=400, content="invalid Google Drive notification headers")
    try:
        hint = gdrive_change_hint(dict(request.headers))
        message_number = int(hint.metadata["message_number"])
        resource_state = hint.event_type
        if message_number > 9_223_372_036_854_775_807:
            raise ValueError
    except Exception:
        return Response(status_code=400, content="invalid Google Drive message number")
    channel = event_store.drive_channel(channel_id)
    if not channel or channel["source_status"] != "active" or channel["project_status"] != "active":
        return Response(status_code=404, content="unknown Google Drive channel")
    supplied_hash = hashlib.sha256(channel_token.encode()).hexdigest()
    if not hmac.compare_digest(str(channel["token_hash"]), supplied_hash):
        return Response(status_code=401, content="invalid Google Drive channel token")
    if channel.get("resource_id") and not hmac.compare_digest(str(channel["resource_id"]), resource_id):
        return Response(status_code=401, content="invalid Google Drive resource id")

    delivery_id = f"{channel_id}:{message_number}"
    try:
        _row_id, inserted = DEFAULT_INBOX.enqueue(
            "gdrive", int(channel["project_id"]), delivery_id,
            {"channel_id": channel_id, "resource_id": resource_id,
             "resource_state": resource_state, "message_number": message_number},
            coalesce_key=f"source:{channel['source_id']}",
        )
    except Exception:
        return Response(status_code=503, content="Google Drive delivery could not be persisted")
    # Repair the early-sync race even on replay: Google may notify before the
    # changes.watch response has populated resource_id.
    event_store.observe_drive_message(channel_id, resource_id, message_number)
    return Response(status_code=204,
                    headers={"X-Mari-Duplicate": "true"} if not inserted else None)


def _apply_poll(source: dict, source_config: dict, poll) -> None:
    source_id = int(source["id"])
    hashes = dict(source_config.get("item_hashes") or {})
    max_tokens, overlap = document_index.chunk_settings()
    with document_index.connection() as conn:
        for document in poll.upserts:
            path = document.external_id
            if not path:
                continue
            title = document.title or path
            body = document.body
            content_hash = document.revision or document_index.content_hash(f"{title}\n\n{body}")
            doc_id, _inserted = document_index.upsert_document(
                conn, source_id, f"gdrive:{source_id}:{path}", title, body,
                f"gdrive/{path}", "page", content_hash, "Google Drive",
                source="gdrive", initials="GD",
                acl_visibility=document.acl.visibility,
                acl_principals=tuple(
                    f"{principal.kind}:{principal.identifier}"
                    for principal in document.acl.principals
                ),
            )
            if hashes.get(path) != content_hash:
                document_index.sync_chunks(conn, doc_id, title, body, max_tokens, overlap)
            hashes[path] = content_hash
        tombstones = {value.external_id for value in poll.tombstones if value.external_id}
        if tombstones:
            rows = document_repository.source_document_paths(conn, source_id)
            gone = [row["id"] for row in rows
                    if str(row.get("source_path") or "").removeprefix("gdrive/") in tombstones]
            document_index.delete_documents(conn, gone)
            for path in tombstones:
                hashes.pop(path, None)
        source_config["item_hashes"] = hashes
        conn.commit()
    if poll.upserts or poll.tombstones:
        invalidate_search(int(source["project_id"]))


def _full_reconcile(source: dict, source_config: dict, channel_id: str) -> None:
    event_store.mark_drive_resync(channel_id)
    result = ingest.run_sync(int(source["id"]), full=True)
    if result is None:
        raise RuntimeError("Google Drive full reconciliation is already running")
    if result.get("error"):
        raise RuntimeError(f"Google Drive full reconciliation failed: {result['error']}")
    refreshed = _source(int(source["id"]), int(source["project_id"]))
    refreshed_config = _json(refreshed["config"])
    cursor = str(refreshed_config.get("cursor") or "")
    if not cursor.startswith("changes:") or not cursor[8:]:
        raise RuntimeError("Google Drive full reconciliation produced no Changes cursor")
    event_store.restore_drive_cursor(source["id"], cursor[8:])


def process_gdrive_delivery(row: dict) -> None:
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    channel = event_store.drive_channel(payload["channel_id"], row["project_id"])
    if not channel or channel["source_status"] != "active" or channel["project_status"] != "active":
        raise RuntimeError("Google Drive source or project is no longer active")
    source_config = _json(channel["config"])
    cursor = str(source_config.get("cursor") or "")
    if not cursor.startswith("changes:") or not cursor[8:]:
        raise RuntimeError("Google Drive source has no Changes cursor; run a full poll first")
    source = {**channel, "id": channel["source_id"], "project_id": channel["project_id"]}
    project_access = access.external_access(
        int(channel["project_id"]), channel["project_slug"], channel["project_name"],
        "gdrive", str(channel["source_id"]), frozenset({"knowledge.read", "knowledge.write"}),
    )
    with access.use_access(project_access):
        try:
            while True:
                request = PollRequest(cursor=cursor, page_limit=1)
                pages = connector_definition("gdrive").poll(
                    source_config, request, http=component_connectors.http_transport,
                )
                poll = next(pages)
                _apply_poll(source, source_config, poll)
                durable = poll.next_cursor if poll.snapshot_complete else poll.next_checkpoint
                if not durable or not str(durable).startswith("changes:"):
                    raise RuntimeError("Google Drive Changes returned no durable cursor")
                cursor = str(durable)
                token = cursor[8:]
                source_config["cursor"] = cursor
                event_store.update_drive_cursor(source["id"], source_config, token)
                if poll.snapshot_complete:
                    break
        except IncompleteSnapshot:
            _full_reconcile(source, source_config, str(channel["channel_id"]))


def renew_due_watches() -> int:
    """Replace channels before Google's expiration and retire elapsed overlap."""
    rows = event_store.due_drive_watches(dt.datetime.now(dt.timezone.utc) + _RENEW_BEFORE)
    renewed = 0
    for row in rows:
        context = access.external_access(
            int(row["project_id"]), row["slug"], row["name"], "service", "gdrive-watch-renewal",
            frozenset({"source.manage"}),
        )
        try:
            create_watch(DriveWatchIn(source_id=int(row["source_id"])), context)
            renewed += 1
        except Exception as exc:  # existing channel remains active until its expiration
            log.warning("Google Drive watch renewal failed for source %s: %s",
                        row["source_id"], exc)
    return renewed


def _renew_loop() -> None:
    while not _renew_stop.is_set():
        try:
            renew_due_watches()
        except Exception:
            log.exception("Google Drive watch renewal pass failed")
        _renew_stop.wait(_RENEW_INTERVAL_SECONDS)


def start_watch_renewal() -> None:
    global _renew_thread
    if _renew_thread is not None and _renew_thread.is_alive():
        return
    _renew_stop.clear()
    _renew_thread = threading.Thread(target=_renew_loop, daemon=True,
                                     name="mari-gdrive-watch-renewal")
    _renew_thread.start()


def stop_watch_renewal(timeout: float = 5.0) -> None:
    global _renew_thread
    _renew_stop.set()
    if _renew_thread is not None:
        _renew_thread.join(timeout)
    _renew_thread = None

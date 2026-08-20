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

import access
import auth
import config
import ingest
from mari_server.infrastructure import connector_provider as component_connectors
from event_inbox import DEFAULT_INBOX
from db import exec_, q, q1
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
    return q1(
        """SELECT s.*, p.slug AS project_slug, p.name AS project_name
             FROM sources s JOIN projects p ON p.id=s.project_id
            WHERE s.id=%s AND s.project_id=%s AND s.kind='connector'
              AND split_part(s.provider, ':', 1)='gdrive'""",
        (source_id, project_id),
    )


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
    exec_(
        """INSERT INTO gdrive_watch_channels
               (project_id, source_id, channel_id, token_hash, page_token, expiration)
             VALUES (%s, %s, %s, %s, %s, %s)""",
        (project_id, source["id"], channel_id, token_hash, page_token, expiration),
    )
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
            http=component_connectors._http,
        )
    except Exception as exc:
        error = str(exc)
        exec_("UPDATE gdrive_watch_channels SET status='error', last_error=%s WHERE channel_id=%s",
              (error[:1000], channel_id))
        raise HTTPException(502, error) from exc
    resource_id = watched.resource_id
    if watched.expiration_ms is not None:
        expiration = dt.datetime.fromtimestamp(watched.expiration_ms / 1000, tz=dt.timezone.utc)
    exec_(
        """UPDATE gdrive_watch_channels
              SET resource_id=%s, expiration=%s, status='active', updated_at=now()
            WHERE channel_id=%s""",
        (resource_id, expiration, channel_id),
    )
    exec_(
        """UPDATE gdrive_watch_channels SET status='retiring', updated_at=now()
            WHERE source_id=%s AND channel_id<>%s AND status='active'""",
        (source["id"], channel_id),
    )
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
    channel = q1(
        """SELECT c.*, s.status AS source_status, p.status AS project_status
             FROM gdrive_watch_channels c
             JOIN sources s ON s.id=c.source_id
             JOIN projects p ON p.id=c.project_id
            WHERE c.channel_id=%s AND c.status IN ('creating','active','retiring')""",
        (channel_id,),
    )
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
    exec_(
        """UPDATE gdrive_watch_channels
              SET resource_id=CASE WHEN resource_id='' THEN %s ELSE resource_id END,
                  last_message_number=GREATEST(last_message_number, %s), updated_at=now()
            WHERE id=%s""",
        (resource_id, message_number, channel["id"]),
    )
    return Response(status_code=204,
                    headers={"X-Mari-Duplicate": "true"} if not inserted else None)


def _apply_poll(source: dict, source_config: dict, poll) -> None:
    source_id = int(source["id"])
    hashes = dict(source_config.get("item_hashes") or {})
    max_tokens, overlap = ingest._chunk_settings()
    with ingest._conn() as conn:
        for document in poll.upserts:
            path = document.external_id
            if not path:
                continue
            title = document.title or path
            body = document.body
            content_hash = document.revision or ingest._sha(f"{title}\n\n{body}")
            doc_id, _inserted = ingest._upsert_document(
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
                ingest._sync_chunks(conn, doc_id, title, body, max_tokens, overlap)
            hashes[path] = content_hash
        tombstones = {value.external_id for value in poll.tombstones if value.external_id}
        if tombstones:
            rows = conn.execute(
                "SELECT id, source_path FROM documents WHERE source_id=%s", (source_id,),
            ).fetchall()
            gone = [row["id"] for row in rows
                    if str(row.get("source_path") or "").removeprefix("gdrive/") in tombstones]
            ingest._delete_documents(conn, gone)
            for path in tombstones:
                hashes.pop(path, None)
        source_config["item_hashes"] = hashes
        conn.commit()


def _full_reconcile(source: dict, source_config: dict, channel_id: str) -> None:
    exec_("""UPDATE gdrive_watch_channels
                SET status='needs_full_resync', last_error='changes token expired (HTTP 410)',
                    updated_at=now() WHERE channel_id=%s""", (channel_id,))
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
    exec_("""UPDATE gdrive_watch_channels
                SET page_token=%s, status='active', last_error='', updated_at=now()
              WHERE source_id=%s AND status IN ('active','needs_full_resync','retiring')""",
          (cursor[8:], source["id"]))


def process_gdrive_delivery(row: dict) -> None:
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    channel = q1(
        """SELECT c.*, s.config, s.provider, s.display_name, s.status AS source_status,
                  p.slug AS project_slug, p.name AS project_name, p.status AS project_status
             FROM gdrive_watch_channels c JOIN sources s ON s.id=c.source_id
             JOIN projects p ON p.id=c.project_id
            WHERE c.channel_id=%s AND c.project_id=%s""",
        (payload["channel_id"], row["project_id"]),
    )
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
                    source_config, request, http=component_connectors._http,
                )
                poll = next(pages)
                _apply_poll(source, source_config, poll)
                durable = poll.next_cursor if poll.snapshot_complete else poll.next_checkpoint
                if not durable or not str(durable).startswith("changes:"):
                    raise RuntimeError("Google Drive Changes returned no durable cursor")
                cursor = str(durable)
                token = cursor[8:]
                source_config["cursor"] = cursor
                exec_("UPDATE sources SET config=%s, last_sync_at=now(), health='Healthy' WHERE id=%s",
                      (json.dumps(source_config), source["id"]))
                exec_("UPDATE gdrive_watch_channels SET page_token=%s, last_error='', updated_at=now() WHERE source_id=%s",
                      (token, source["id"]))
                if poll.snapshot_complete:
                    break
        except IncompleteSnapshot:
            _full_reconcile(source, source_config, str(channel["channel_id"]))


def renew_due_watches() -> int:
    """Replace channels before Google's expiration and retire elapsed overlap."""
    exec_("""DELETE FROM gdrive_watch_channels
              WHERE status='retiring' AND expiration IS NOT NULL AND expiration < now()""")
    rows = q(
        """SELECT c.source_id, c.project_id, p.slug, p.name
             FROM gdrive_watch_channels c JOIN projects p ON p.id=c.project_id
            WHERE c.status='active' AND c.expiration IS NOT NULL
              AND c.expiration <= %s AND p.status='active'
            ORDER BY c.expiration""",
        (dt.datetime.now(dt.timezone.utc) + _RENEW_BEFORE,),
    )
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

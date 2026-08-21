"""Durable GitHub and Confluence change-hint ingestion.

Provider payloads are authenticated and reduced to bounded dirty hints before
they enter ``event_inbox``.  Workers never index webhook content: they resolve
the project/source again and fetch canonical provider state.  Scheduled polls
remain the reconciliation path for missed or out-of-order deliveries.
"""

from __future__ import annotations

import json
import typing as t

from fastapi import APIRouter, Depends, HTTPException, Request

from mari_server.identity import access
from mari_server.identity import routes as auth
from mari_server.providers import connectors as component_connectors
from mari_server.sources import sync as ingest
from mari_server.persistence.postgres import document_index
from mari_server.persistence.postgres import provider_events as event_store
from mari_server.persistence.postgres import documents as document_repository
from mari_server.search.service import invalidate_search
from mari_server.persistence.postgres.event_inbox import DEFAULT_INBOX
from mari_components.connectors import ConfluenceConfig, fetch_confluence_page
from mari_components.connectors.events import (
    MAX_DIRTY_PATHS, confluence_change_hint, github_change_hint,
    verify_hmac_sha256,
)


router = APIRouter()
INBOX = DEFAULT_INBOX
MAX_WEBHOOK_BYTES = 1_048_576


def _json(value: t.Any) -> dict[str, t.Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _signed(raw: bytes, supplied: str, secret: str) -> bool:
    try:
        verify_hmac_sha256(raw, supplied, secret)
        return True
    except Exception:
        return False


async def _body(request: Request) -> bytes:
    length = request.headers.get("content-length", "")
    if length:
        try:
            if int(length) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "webhook payload is too large")
        except ValueError:
            raise HTTPException(400, "invalid content length") from None
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "webhook payload is too large")
    return raw


def _payload(raw: bytes) -> dict[str, t.Any]:
    try:
        value = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON payload") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "webhook payload must be an object")
    return value


def _github_hint(event: str, payload: dict[str, t.Any]) -> dict[str, t.Any]:
    component = github_change_hint(event, payload)
    repository = str(_json(payload.get("repository")).get("full_name") or "")[:300]
    hint: dict[str, t.Any] = {
        "event": component.event_type,
        "repository": repository,
        **dict(component.metadata),
    }
    if isinstance(hint.get("paths"), tuple):
        hint["paths"] = list(hint["paths"])
    return hint


@router.post("/webhooks/github")
async def github_webhook(request: Request):
    raw = await _body(request)
    payload = _payload(raw)
    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    event = request.headers.get("X-GitHub-Event", "").strip()
    if not delivery_id or len(delivery_id) > 200 or not event:
        raise HTTPException(400, "GitHub delivery and event headers are required")
    external_id = str(_json(payload.get("installation")).get("id") or "")
    installation = event_store.github_installation(external_id)
    if not installation:
        raise HTTPException(401, "unknown GitHub installation")
    cfg = _json(installation.get("config"))
    if not _signed(raw, request.headers.get("X-Hub-Signature-256", ""),
                   str(cfg.get("webhook_secret") or "")):
        raise HTTPException(401, "bad GitHub signature")

    hint = _github_hint(event, payload)
    repository = hint["repository"]
    if not repository:
        if event == "ping":
            return {"ok": True, "queued": False}
        raise HTTPException(400, "GitHub repository is required")
    source = event_store.github_source(installation["project_id"], repository)
    if not source:
        raise HTTPException(404, "repository is not connected to this installation")
    envelope = {
        "installation_id": int(installation["id"]),
        "source_id": int(source["id"]),
        "hint": hint,
    }
    try:
        row_id, inserted = INBOX.enqueue(
            "github", int(installation["project_id"]), delivery_id, envelope,
            coalesce_key=f"source:{source['id']}",
        )
    except Exception as exc:
        raise HTTPException(503, "could not durably store GitHub delivery") from exc
    return {"ok": True, "queued": inserted, "event_id": row_id}


def _confluence_hint(payload: dict[str, t.Any]) -> dict[str, str]:
    component = confluence_change_hint(payload)
    return {
        "event": component.event_type,
        "page_id": component.external_id,
        "space_key": str(component.metadata.get("space_key") or ""),
    }


@router.post("/webhooks/confluence/{source_id}")
async def confluence_webhook(source_id: int, request: Request):
    raw = await _body(request)
    payload = _payload(raw)
    delivery_id = request.headers.get("X-Atlassian-Webhook-Identifier", "").strip()
    if not delivery_id or len(delivery_id) > 200:
        raise HTTPException(400, "Atlassian webhook identifier is required")
    source = event_store.confluence_source(source_id)
    if not source:
        raise HTTPException(404, "Confluence source not found")
    cfg = _json(source.get("config"))
    if not _signed(raw, request.headers.get("X-Mari-Signature-256", ""),
                   str(cfg.get("webhook_secret") or "")):
        raise HTTPException(401, "bad Confluence signature")
    hint = _confluence_hint(payload)
    configured_space = str(cfg.get("space_key") or "").strip()
    if configured_space and hint["space_key"] and hint["space_key"] != configured_space:
        raise HTTPException(403, "event is outside the configured Confluence space")
    if not hint["page_id"] and not hint["space_key"]:
        raise HTTPException(400, "Confluence page or space hint is required")
    try:
        row_id, inserted = INBOX.enqueue(
            "confluence", int(source["project_id"]), delivery_id,
            {"source_id": int(source_id), "hint": hint},
            coalesce_key=f"source:{source_id}",
        )
    except Exception as exc:
        raise HTTPException(503, "could not durably store Confluence delivery") from exc
    return {"ok": True, "queued": inserted, "event_id": row_id}


@router.get(
    "/connectors/confluence/{source_id}/webhook",
)
def confluence_webhook_setup(
    source_id: int,
    request: Request,
    current: access.AccessContext = Depends(auth.require_capability("source.manage")),
):
    source = event_store.confluence_source(source_id, current.project_id)
    if not source:
        raise HTTPException(404, "Confluence source not found")
    configured = bool(str(_json(source.get("config")).get("webhook_secret") or ""))
    return {
        "url": str(request.base_url).rstrip("/") + f"/webhooks/confluence/{source_id}",
        "signature_header": "X-Mari-Signature-256",
        "delivery_header": "X-Atlassian-Webhook-Identifier",
        "algorithm": "HMAC-SHA256",
        "configured": configured,
    }


def _source(source_id: int, project_id: int, *, kind: str, provider: str | None = None):
    return event_store.source(source_id, project_id, kind, provider)


def _worker_access(source: dict[str, t.Any], provider: str):
    return access.external_access(
        int(source["project_id"]), str(source["project_slug"]), str(source["project_name"]),
        provider, str(source["id"]), frozenset({"source.sync"}),
    )


def process_github_delivery(row: dict[str, t.Any]) -> None:
    envelope = _json(row.get("payload"))
    if not event_store.installation_active(int(envelope.get("installation_id") or 0),
                                           int(row["project_id"]), "github"):
        raise RuntimeError("GitHub installation is no longer active")
    source = _source(
        int(envelope.get("source_id") or 0), int(row["project_id"]),
        kind="connector", provider="github",
    )
    if not source:
        raise RuntimeError("GitHub source is no longer active")
    expected_repo = str(_json(source.get("config")).get("repo") or "")
    if expected_repo != str(_json(envelope.get("hint")).get("repository") or ""):
        raise RuntimeError("GitHub delivery does not match its routed source")
    with access.use_access(_worker_access(source, "github")):
        result = ingest.run_sync(int(source["id"]), False)
    if result is None:
        raise RuntimeError("GitHub source sync is already running")
    if result.get("error"):
        raise RuntimeError(str(result["error"]))


def _sync_confluence_page(source: dict[str, t.Any], page_id: str) -> None:
    cfg = _json(source.get("config"))
    document = fetch_confluence_page(
        ConfluenceConfig(
            str(cfg.get("site_url") or ""), str(cfg.get("email") or ""),
            str(cfg.get("api_token") or ""), str(cfg.get("space_key") or ""),
        ),
        page_id,
        http=component_connectors.http_transport,
    )
    configured_space = str(cfg.get("space_key") or "").strip()
    if (document is not None and configured_space
            and str(document.metadata.get("space_key") or "") != configured_space):
        document = None
    path = str(page_id)
    hashes = dict(cfg.get("item_hashes") or {})
    with document_index.connection() as conn:
        if document is None:
            document_index.delete_documents(conn, document_repository.ids_for_source_path(
                conn, source["project_id"], source["id"], f"confluence/{path}"))
            hashes.pop(path, None)
        else:
            title = document.title or path
            body = document.body
            content_hash = document.revision or document_index.content_hash(f"{title}\n\n{body}")
            doc_id, _ = document_index.upsert_document(
                conn, int(source["id"]), f"confluence:{source['id']}:{path}", title, body,
                f"confluence/{path}", "page", content_hash, "Confluence",
                source="confluence", initials="CO", acl_visibility="connector_scope",
                source_updated_at=document.updated_at,
            )
            max_tokens, overlap = document_index.chunk_settings()
            if body.strip():
                document_index.sync_chunks(conn, doc_id, title, body, max_tokens, overlap)
            else:
                document_repository.clear_derived_content(conn, doc_id)
            hashes[path] = content_hash
        cfg["item_hashes"] = hashes
        document_repository.finalize_source(conn, source["project_id"], source["id"], cfg)
        conn.commit()
    invalidate_search(int(source["project_id"]))


def process_confluence_delivery(row: dict[str, t.Any]) -> None:
    envelope = _json(row.get("payload"))
    source = _source(
        int(envelope.get("source_id") or 0), int(row["project_id"]),
        kind="connector", provider="confluence",
    )
    if not source:
        raise RuntimeError("Confluence source is no longer active")
    hint = _json(envelope.get("hint"))
    with access.use_access(_worker_access(source, "confluence")):
        if hint.get("page_id"):
            _sync_confluence_page(source, str(hint["page_id"]))
        else:
            result = ingest.run_sync(int(source["id"]), False)
            if result is None:
                raise RuntimeError("Confluence source sync is already running")
            if result.get("error"):
                raise RuntimeError(str(result["error"]))


HANDLERS = {
    "github": process_github_delivery,
    "confluence": process_confluence_delivery,
}

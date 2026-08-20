"""HTTP adapter for infrastructure-neutral connector components."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Callable, Iterator

from connectors import _net
from connectors._protocol import ACLMetadata, PollResult
from mari_components import PollPage as ComponentPollPage, PollRequest, SyncMode
from mari_components.connectors import CONNECTOR_CATALOG, connector_definition
from mari_components.http import HttpRequest, HttpResponse


_ENVELOPE = "mari-components:"


def _http(request: HttpRequest) -> HttpResponse:
    response = _net.fetch(
        request.url,
        method=request.method,
        headers=dict(request.headers),
        data=request.body,
        timeout=request.timeout,
    )
    return HttpResponse(response.status, dict(response.headers), response.body)


def _cursor(value: str | None) -> tuple[str | None, str | None]:
    if not value or not str(value).startswith(_ENVELOPE):
        return value, None
    try:
        payload = json.loads(str(value)[len(_ENVELOPE):])
        return payload.get("cursor"), payload.get("checkpoint")
    except (AttributeError, json.JSONDecodeError):
        raise ValueError("invalid connector checkpoint envelope") from None


def _envelope(cursor: str | None, checkpoint: str | None) -> str:
    return _ENVELOPE + json.dumps(
        {"cursor": cursor, "checkpoint": checkpoint}, sort_keys=True, separators=(",", ":"))


def _item(document) -> dict:
    return {
        "path": document.external_id,
        "title": document.title,
        "body": document.body,
        "updated_at": document.updated_at,
        "hash_hint": document.revision or None,
        "source_url": document.source_url or None,
        "acl": ACLMetadata(
            visibility=document.acl.visibility,
            principals=tuple(
                f"{principal.kind}:{principal.identifier}" for principal in document.acl.principals),
        ),
        **dict(document.metadata),
    }


def _collect(pages: Iterator, original_cursor: str | None) -> PollResult:
    items: list[dict] = []
    tombstones: list[str] = []
    last = None
    for page in pages:
        last = page
        items.extend(_item(document) for document in page.upserts)
        tombstones.extend(tombstone.external_id for tombstone in page.tombstones)
    if last is None:
        raise RuntimeError("connector returned no polling page")
    checkpoint = None
    cursor = last.next_cursor
    if not last.snapshot_complete:
        cursor = original_cursor
        checkpoint = _envelope(original_cursor, last.next_checkpoint)
    return PollResult(
        items,
        cursor,
        snapshot_complete=last.snapshot_complete,
        tombstones=sorted(set(tombstones)),
        checkpoint=checkpoint,
    )


def _request(cursor: str | None, cfg: dict, *, full: bool = False) -> tuple[PollRequest, str | None]:
    base_cursor, checkpoint = _cursor(cursor)
    return PollRequest(
        mode=SyncMode.FULL if full or base_cursor is None else SyncMode.INCREMENTAL,
        cursor=base_cursor,
        checkpoint=checkpoint,
        page_size=max(1, min(int(cfg.get("page_size") or 100), 200)),
        page_limit=max(1, min(int(cfg.get("page_limit") or 20), 100)),
    ), base_cursor


def poll_pages(key: str, cfg: dict, cursor: str | None, *, full: bool = False):
    """Yield native pages while encoding Mari's single-field checkpoint state."""
    definition = connector_definition(key)
    request, base_cursor = _request(cursor, cfg, full=full)
    for page in definition.poll(cfg, request, http=_http):
        if not page.snapshot_complete:
            page = replace(
                page,
                next_cursor=base_cursor,
                next_checkpoint=_envelope(base_cursor, page.next_checkpoint),
            )
        yield page


def functions(key: str, legacy_validate: Callable, legacy_list: Callable) -> tuple[Callable, Callable]:
    """Return component-backed functions or the untouched excluded provider."""
    if key not in CONNECTOR_CATALOG:
        return legacy_validate, legacy_list
    definition = connector_definition(key)

    def validate(cfg: dict) -> str | None:
        try:
            result = definition.validate(cfg, http=_http)
            return None if result.ok else result.message
        except Exception as error:
            return str(error)

    def list_items(cfg: dict, cursor: str | None) -> PollResult:
        request, base_cursor = _request(cursor, cfg)
        return _collect(definition.poll(cfg, request, http=_http), base_cursor)

    return validate, list_items


def validate_config(key: str, values: dict) -> str | None:
    """Validate raw connector values through the shared declarative catalog."""
    definition = connector_definition(key)
    result = definition.validate(values, http=_http)
    return None if result.ok else result.message

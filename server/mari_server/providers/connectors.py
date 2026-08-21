"""HTTP adapter for infrastructure-neutral connector components."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterator

from mari_server.providers import http as nethttp
from mari_components import PollRequest, SyncMode
from mari_components.connectors import connector_definition
from mari_components.http import HttpRequest, HttpResponse


def http_transport(request: HttpRequest) -> HttpResponse:
    response = nethttp.fetch(
        request.url,
        method=request.method,
        headers=dict(request.headers),
        data=request.body,
        timeout=request.timeout,
    )
    return HttpResponse(response.status, dict(response.headers), response.body)


def request(cursor: str | None, checkpoint: str | None, cfg: dict, *, full: bool = False) -> PollRequest:
    return PollRequest(
        mode=SyncMode.FULL if full or cursor is None else SyncMode.INCREMENTAL,
        cursor=cursor,
        checkpoint=checkpoint,
        page_size=max(1, min(int(cfg.get("page_size") or 100), 200)),
        page_limit=max(1, min(int(cfg.get("page_limit") or 20), 100)),
    )


def poll_pages(key: str, cfg: dict, cursor: str | None, checkpoint: str | None = None,
               *, full: bool = False):
    """Yield the shared connector's native pages without a legacy translation."""
    definition = connector_definition(key)
    poll_request = request(cursor, checkpoint, cfg, full=full)
    for page in definition.poll(cfg, poll_request, http=http_transport):
        if not page.snapshot_complete:
            page = replace(
                page,
                next_cursor=cursor,
            )
        yield page


def validate_config(key: str, values: dict) -> str | None:
    """Validate raw connector values through the shared declarative catalog."""
    definition = connector_definition(key)
    result = definition.validate(values, http=http_transport)
    return None if result.ok else result.message

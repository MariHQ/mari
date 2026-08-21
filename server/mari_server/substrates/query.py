"""Product-facing reads over the explicitly selected knowledge substrate."""

from __future__ import annotations

import threading
import time

from mari_components.substrates import SearchRequest
from mari_server.identity import context as access
from mari_server.persistence.postgres import substrate_references
from mari_server.persistence.postgres.database import actor_name
from mari_server.search.service import hybrid_count, hybrid_search

from .service import configured_substrate

_CACHE_TTL_SECONDS = 120.0
_CACHE_LIMIT = 128
_cache: dict[tuple[int, str, str], tuple[float, list]] = {}
_key_locks: dict[tuple[int, str, str], threading.Lock] = {}
_cache_lock = threading.Lock()


def _external_hits(project_id: int, provider: str, query: str) -> list:
    """Fetch one stable result set for the list and count fields in a request."""
    key = (project_id, provider, query)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:
        with _cache_lock:
            cached = _cache.get(key)
            if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
                return cached[1]
        try:
            hits = configured_substrate().search(SearchRequest(query, 100, 0))
            with _cache_lock:
                _cache[key] = (time.monotonic(), hits)
                if len(_cache) > _CACHE_LIMIT:
                    oldest = min(_cache, key=lambda value: _cache[value][0])
                    _cache.pop(oldest, None)
            return hits
        finally:
            with _cache_lock:
                _key_locks.pop(key, None)


def _row(value: dict) -> dict:
    project_id = access.require_current_access().project_id
    return {
        "id": int(value["id"]), "source": value["source"], "title": value["title"],
        "snippet": value["excerpt"], "body": value["excerpt"], "kind": "reference",
        "author": value["substrate"], "author_initials": value["substrate"][:2].upper(),
        "updated_src": value.get("updated_at"),
        "tags": substrate_references.tags(project_id, int(value["id"])),
        "watched": substrate_references.is_watched(project_id, int(value["id"]), actor_name()),
        "source_url": value["url"], "external_id": value["external_id"],
    }


def search(query: str, limit: int = 10, offset: int = 0) -> list[dict]:
    substrate = configured_substrate()
    info = substrate.info()
    if info.provider == "native":
        return hybrid_search(query, limit, offset)
    project_id = access.require_current_access().project_id
    hits = _external_hits(project_id, info.provider, query)[offset:offset + limit]
    rows = substrate_references.record_hits(
        project_id, info.provider, hits,
    )
    return [_row(row) for row in rows]


def count(query: str) -> int:
    substrate = configured_substrate()
    if substrate.info().provider == "native":
        return hybrid_count(query)
    project_id = access.require_current_access().project_id
    return len(_external_hits(project_id, substrate.info().provider, query))


def recent(limit: int, offset: int = 0) -> list[dict]:
    substrate = configured_substrate()
    if substrate.info().provider == "native":
        from mari_server.persistence.postgres import documents
        return documents.recent(limit, offset)
    rows = substrate_references.recent_documents(
        access.require_current_access().project_id, limit, offset,
    )
    return [_row(row) for row in rows]


def get(document_id: int) -> dict | None:
    substrate = configured_substrate()
    if substrate.info().provider == "native":
        from mari_server.persistence.postgres import documents
        return documents.get(document_id)
    row = substrate_references.get_document(
        access.require_current_access().project_id, document_id,
    )
    return _row(row) if row else None

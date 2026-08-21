"""Product-neutral HTTP surface over the selected knowledge substrate."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mari_components.substrates import Document, SearchRequest, SourceRegistration, TextSection
from mari_server.identity import access

from .errors import SubstrateConfigurationError, SubstrateRequestError
from .service import configured_substrate

router = APIRouter(prefix="/api/knowledge-substrate", tags=["knowledge-substrate"])
_read = [Depends(access.require_capability("knowledge.read"))]
_knowledge_write = [Depends(access.require_capability("knowledge.write"))]
_source_write = [Depends(access.require_capability("source.manage"))]


def _call(operation):
    try:
        return operation()
    except SubstrateConfigurationError as error:
        raise HTTPException(409, str(error)) from None
    except SubstrateRequestError as error:
        status = error.status if error.status in {400, 401, 403, 404, 409, 422, 429, 503} else 502
        raise HTTPException(status, str(error)) from None


def _time(value: dt.datetime | None) -> str | None:
    return value.astimezone(dt.timezone.utc).isoformat() if value else None


def _source(value) -> dict[str, Any]:
    return {
        "id": value.source_id,
        "name": value.name,
        "kind": value.kind,
        "status": value.status,
        "credentialId": value.credential_id,
        "documentCount": value.document_count,
        "lastRunAt": _time(value.last_run_at),
        "error": value.error,
        "configuration": dict(value.configuration),
    }


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2048)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sources: list[str] = Field(default_factory=list)
    tags: dict[str, list[str]] = Field(default_factory=dict)
    updated_after: dt.datetime | None = None


class SectionIn(BaseModel):
    text: str = Field(min_length=1)
    url: str = ""
    heading: str = ""


class DocumentIn(BaseModel):
    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    sections: list[SectionIn] = Field(min_length=1)
    updated_at: dt.datetime | None = None
    created_at: dt.datetime | None = None
    metadata: dict[str, str | list[str]] = Field(default_factory=dict)


class SourceIn(BaseModel):
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    configuration: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    refresh_seconds: int | None = Field(default=None, ge=60)
    prune_seconds: int | None = Field(default=None, ge=60)
    access: str = "public"
    groups: list[int] = Field(default_factory=list)


@router.get("", dependencies=_read)
def status() -> dict[str, Any]:
    info = _call(lambda: configured_substrate().info())
    return {
        "provider": info.provider,
        "version": info.version,
        "healthy": info.healthy,
        "detail": info.detail,
        "capabilities": sorted(value.value for value in info.capabilities),
    }


@router.post("/search", dependencies=_read)
def search(body: SearchIn) -> dict[str, Any]:
    request = SearchRequest(
        body.query,
        body.limit,
        body.offset,
        tuple(body.sources),
        {key: tuple(values) for key, values in body.tags.items()},
        body.updated_after,
    )
    hits = _call(lambda: configured_substrate().search(request))
    return {"results": [{
        "id": hit.document_id,
        "citationId": hit.citation_id,
        "title": hit.title,
        "content": hit.content,
        "source": hit.source,
        "url": hit.url,
        "updatedAt": _time(hit.updated_at),
        "score": hit.score,
        "metadata": dict(hit.metadata),
    } for hit in hits]}


@router.post("/documents", dependencies=_knowledge_write)
def upsert_document(body: DocumentIn) -> dict[str, Any]:
    metadata = {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in body.metadata.items()
    }
    result = _call(lambda: configured_substrate().upsert_document(Document(
        body.external_id,
        body.title,
        body.source,
        tuple(TextSection(section.text, section.url, section.heading) for section in body.sections),
        body.updated_at,
        body.created_at,
        metadata,
    )))
    return {"id": result.document_id, "created": result.created}


@router.delete("/documents/{document_id:path}", dependencies=_knowledge_write, status_code=204)
def delete_document(document_id: str) -> None:
    _call(lambda: configured_substrate().delete_document(document_id))


@router.get("/sources", dependencies=_read)
def sources() -> dict[str, Any]:
    return {"sources": [_source(value) for value in _call(
        lambda: configured_substrate().list_sources()
    )]}


@router.post("/sources", dependencies=_source_write)
def create_source(body: SourceIn) -> dict[str, Any]:
    value = _call(lambda: configured_substrate().create_source(SourceRegistration(
        body.name,
        body.kind,
        body.configuration,
        body.credentials,
        body.refresh_seconds,
        body.prune_seconds,
        body.access,
        tuple(body.groups),
    )))
    return _source(value)


@router.post("/sources/{source_id}/runs", dependencies=_source_write)
def run_source(source_id: str, full: bool = False) -> dict[str, str]:
    return {"runId": _call(lambda: configured_substrate().run_source(source_id, full=full))}


@router.delete("/sources/{source_id}", dependencies=_source_write, status_code=204)
def delete_source(source_id: str) -> None:
    _call(lambda: configured_substrate().delete_source(source_id))

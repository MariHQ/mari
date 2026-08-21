"""Onyx Community Edition adapter for the generic substrate port."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from mari_components.substrates import (
    Capability,
    Document,
    SearchHit,
    SearchRequest,
    Source,
    SourceRegistration,
    SubstrateInfo,
    UpsertResult,
)

from .errors import SubstrateConfigurationError, SubstrateRequestError

Transport = Callable[[str, str, Mapping[str, Any] | None], Any]


def _timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)


class OnyxSubstrate:
    """HTTP integration with Onyx CE's supported public API surface."""

    _CAPABILITIES = frozenset({
        Capability.SEARCH,
        Capability.DOCUMENT_WRITE,
        Capability.SOURCE_READ,
        Capability.SOURCE_WRITE,
        Capability.SOURCE_RUN,
    })

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0,
                 search_mode: str = "keyword",
                 transport: Transport | None = None):
        base_url = base_url.strip().rstrip("/")
        if not base_url:
            raise SubstrateConfigurationError("MARI_ONYX_URL is required when Onyx is selected.")
        if not api_key.strip():
            raise SubstrateConfigurationError("MARI_ONYX_API_KEY is required when Onyx is selected.")
        self.base_url = base_url
        self.api_key = api_key.strip()
        self.timeout = max(1.0, float(timeout))
        self.search_mode = search_mode.strip().lower()
        if self.search_mode not in {"keyword", "agentic"}:
            raise SubstrateConfigurationError("Onyx search mode must be 'keyword' or 'agentic'.")
        self._transport = transport or self._request

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            # Never reflect a vendor body: it can contain internal data and the
            # configured bearer must never appear in application errors.
            raise SubstrateRequestError(error.code, f"{method} {path}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SubstrateRequestError(503, f"{method} {path}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise SubstrateRequestError(502, f"{method} {path}") from None

    def info(self) -> SubstrateInfo:
        try:
            response = self._transport("GET", "/api/health", None)
            healthy = not isinstance(response, dict) or response.get("success", True) is not False
            return SubstrateInfo("onyx", "community-api", self._CAPABILITIES, healthy)
        except SubstrateRequestError as error:
            return SubstrateInfo("onyx", "community-api", self._CAPABILITIES, False, str(error))

    def search(self, request: SearchRequest) -> list[SearchHit]:
        if self.search_mode == "keyword":
            return self._keyword_search(request)
        return self._agentic_search(request)

    def _keyword_search(self, request: SearchRequest) -> list[SearchHit]:
        filters: dict[str, Any] = {}
        if request.sources:
            filters["source_type"] = list(request.sources)
        if request.tags:
            filters["tags"] = [
                {"tag_key": key, "tag_value": value}
                for key, values in request.tags.items() for value in values
            ]
        if request.updated_after:
            filters["updated_at_range"] = {
                "start": request.updated_after.astimezone(dt.timezone.utc).isoformat(),
            }
        payload = self._transport("POST", "/api/admin/search", {
            "query": request.query,
            "filters": filters,
        }) or {}
        raw_results = payload.get("documents", []) if isinstance(payload, dict) else []
        hits = [SearchHit(
            document_id=str(row.get("document_id") or ""),
            title=str(row.get("semantic_identifier") or "Untitled"),
            content=str(row.get("blurb") or ""),
            source=str(row.get("source_type") or "onyx"),
            url=str(row.get("link") or ""),
            updated_at=_timestamp(row.get("updated_at")),
            score=float(row["score"]) if row.get("score") is not None else None,
            metadata=row.get("metadata") or {},
        ) for row in raw_results if isinstance(row, dict) and row.get("document_id")]
        return hits[request.offset:request.offset + request.limit]

    def _agentic_search(self, request: SearchRequest) -> list[SearchHit]:
        body: dict[str, Any] = {
            "query": request.query,
            # Avoid expansion, but Onyx may still use its configured LLM for
            # automatic source/time filters. This mode is opt-in only.
            "skip_query_expansion": True,
        }
        if request.sources:
            body["sources"] = list(request.sources)
        if request.tags:
            body["tags"] = [
                {"tag_key": key, "tag_value": value}
                for key, values in request.tags.items() for value in values
            ]
        if request.updated_after:
            body["time_cutoff"] = request.updated_after.astimezone(dt.timezone.utc).isoformat()
        payload = self._transport("POST", "/api/search", body) or {}
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        hits: list[SearchHit] = []
        for row in raw_results:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "Untitled")
            content = str(row.get("content") or "")
            source = str(row.get("source_type") or "onyx")
            url = str(row.get("link") or "")
            # Onyx's agentic search response exposes a per-query citation number but
            # not its canonical document id. Derive a stable evidence reference
            # from source + URL (or title when a connector supplies no URL).
            identity = f"{source}\0{url or title}"
            document_id = "onyx:" + hashlib.sha256(identity.encode()).hexdigest()
            hits.append(SearchHit(
                document_id=document_id,
                citation_id=str(row.get("citation_id") or ""),
                title=title,
                content=content,
                source=source,
                url=url,
                updated_at=_timestamp(row.get("updated_at")),
            ))
        return hits[request.offset:request.offset + request.limit]

    def upsert_document(self, document: Document) -> UpsertResult:
        metadata = {
            key: list(value) if isinstance(value, tuple) else str(value)
            for key, value in document.metadata.items()
        }
        payload = self._transport("POST", "/api/onyx-api/ingestion", {
            "document": {
                "id": document.external_id,
                "sections": [{
                    "type": "text",
                    "text": section.text,
                    **({"link": section.url} if section.url else {}),
                    **({"heading": section.heading} if section.heading else {}),
                } for section in document.sections],
                "source": document.source,
                "semantic_identifier": document.title,
                "title": document.title,
                "metadata": metadata,
                "doc_updated_at": document.updated_at.isoformat() if document.updated_at else None,
                "doc_created_at": document.created_at.isoformat() if document.created_at else None,
            },
        }) or {}
        return UpsertResult(
            document_id=str(payload.get("document_id") or document.external_id),
            created=not bool(payload.get("already_existed")),
        )

    def delete_document(self, document_id: str) -> None:
        encoded = urllib.parse.quote(document_id, safe="")
        self._transport("DELETE", f"/api/onyx-api/ingestion/{encoded}", None)

    def list_sources(self) -> list[Source]:
        payload = self._transport("GET", "/api/manage/admin/connector/status", None) or []
        status_payload = self._transport("POST", "/api/manage/admin/connector/indexing-status", {
            "get_all_connectors": True,
        }) or []
        statuses = {
            str(item.get("cc_pair_id")): item
            for group in status_payload if isinstance(group, dict)
            for item in group.get("indexing_statuses", []) if isinstance(item, dict)
        }
        sources: list[Source] = []
        for row in payload if isinstance(payload, list) else []:
            if not isinstance(row, dict):
                continue
            connector = row.get("connector") if isinstance(row.get("connector"), dict) else {}
            credential = row.get("credential") if isinstance(row.get("credential"), dict) else {}
            source_id = str(row.get("cc_pair_id") or row.get("id") or "")
            state = statuses.get(source_id, {})
            if state.get("in_progress"):
                status = "syncing"
            elif state.get("in_repeated_error_state") or state.get("last_status") == "failed":
                status = "error"
            else:
                status = str(state.get("cc_pair_status") or "active").lower()
            sources.append(Source(
                source_id=source_id,
                name=str(row.get("name") or connector.get("name") or "Unnamed source"),
                kind=str(connector.get("source") or "unknown"),
                status=status,
                credential_id=str(credential.get("id") or ""),
                document_count=(int(state["docs_indexed"])
                                if state.get("docs_indexed") is not None else None),
                last_run_at=_timestamp(state.get("last_success")),
                error=("Latest ingestion failed" if status == "error" else ""),
                configuration=connector.get("connector_specific_config") or {},
            ))
        return sources

    def create_source(self, registration: SourceRegistration) -> Source:
        connector = self._transport("POST", "/api/manage/admin/connector", {
            "name": registration.name,
            "source": registration.kind,
            "input_type": "poll",
            "connector_specific_config": dict(registration.configuration),
            "refresh_freq": registration.refresh_seconds,
            "prune_freq": registration.prune_seconds,
            "indexing_start": None,
            "access_type": registration.access,
            "groups": list(registration.groups),
        }) or {}
        connector_id = str(connector.get("id") or "")
        if not connector_id:
            raise SubstrateRequestError(502, "create connector")
        credential = self._transport("POST", "/api/manage/credential", {
            "credential_json": dict(registration.credentials),
            "admin_public": True,
            "curator_public": False,
            "groups": list(registration.groups),
            "source": registration.kind,
            "name": registration.name,
        }) or {}
        credential_id = str(credential.get("id") or "")
        if not credential_id:
            raise SubstrateRequestError(502, "create credential")
        association = self._transport(
            "PUT",
            f"/api/manage/connector/{urllib.parse.quote(connector_id)}/credential/{urllib.parse.quote(credential_id)}",
            {
                "name": registration.name,
                "access_type": registration.access,
                "groups": list(registration.groups),
                "auto_sync_options": None,
                "processing_mode": "REGULAR",
            },
        ) or {}
        source_id = str(association.get("data") or "")
        if not source_id:
            raise SubstrateRequestError(502, "associate connector credential")
        return Source(source_id, registration.name, registration.kind, "active", credential_id,
                      configuration=dict(registration.configuration))

    def run_source(self, source_id: str, *, full: bool = False) -> str:
        connector_id, credential_id = self._source_ids(source_id)
        payload = self._transport("POST", "/api/manage/admin/connector/run-once", {
            # Onyx triggers by connector id, not cc-pair id. Resolve it from
            # the status surface so callers only handle the generic source id.
            "connector_id": connector_id,
            "credential_ids": [credential_id],
            "from_beginning": bool(full),
        }) or {}
        return str(payload.get("data") or payload.get("message") or "scheduled")

    def _source_ids(self, source_id: str) -> tuple[int, int]:
        payload = self._transport("GET", f"/api/manage/admin/cc-pair/{urllib.parse.quote(source_id)}", None) or {}
        connector = payload.get("connector") if isinstance(payload, dict) else None
        credential = payload.get("credential") if isinstance(payload, dict) else None
        connector_id = connector.get("id") if isinstance(connector, dict) else None
        credential_id = credential.get("id") if isinstance(credential, dict) else None
        if connector_id is None or credential_id is None:
            raise SubstrateRequestError(404, "resolve source")
        return int(connector_id), int(credential_id)

    def delete_source(self, source_id: str) -> None:
        connector_id, credential_id = self._source_ids(source_id)
        self._transport(
            "DELETE", f"/api/manage/connector/{connector_id}/credential/{credential_id}", None,
        )

    def pause_source(self, source_id: str) -> None:
        self._transport(
            "PUT", f"/api/manage/admin/cc-pair/{urllib.parse.quote(source_id)}/status",
            {"status": "PAUSED"},
        )

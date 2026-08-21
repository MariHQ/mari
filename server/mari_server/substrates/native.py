"""Adapter for Mari's built-in retrieval engine."""

from __future__ import annotations

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

from mari_server.search.service import hybrid_search

from .errors import SubstrateConfigurationError


class NativeSubstrate:
    """Current in-process search, selected explicitly as ``native``."""

    def info(self) -> SubstrateInfo:
        return SubstrateInfo("native", "1", frozenset({Capability.SEARCH}), True)

    def search(self, request: SearchRequest) -> list[SearchHit]:
        rows = hybrid_search(request.query, request.limit, request.offset)
        return [SearchHit(
            document_id=str(row["id"]),
            title=str(row.get("title") or "Untitled"),
            content=str(row.get("body") or row.get("snippet") or ""),
            source=str(row.get("source") or "native"),
            url=str(row.get("source_url") or ""),
            updated_at=row.get("updated_src"),
            score=float(row.get("score") or 0),
        ) for row in rows]

    @staticmethod
    def _unsupported(operation: str):
        raise SubstrateConfigurationError(
            f"The native substrate does not implement generic {operation}; use Mari's source APIs."
        )

    def upsert_document(self, document: Document) -> UpsertResult:
        return self._unsupported("document writes")

    def delete_document(self, document_id: str) -> None:
        self._unsupported("document deletion")

    def list_sources(self) -> list[Source]:
        return self._unsupported("source listing")

    def create_source(self, registration: SourceRegistration) -> Source:
        return self._unsupported("source creation")

    def run_source(self, source_id: str, *, full: bool = False) -> str:
        return self._unsupported("source execution")

    def pause_source(self, source_id: str) -> None:
        self._unsupported("source pausing")

    def delete_source(self, source_id: str) -> None:
        self._unsupported("source deletion")

"""Canonical, immutable knowledge lifecycle stored in Apache Iceberg.

Each source revision is an append-only version. Active/archived/deleted are
states in that history, not destructive row operations. Search embeddings are
intentionally absent: retrieval.py can rebuild and flush those derived files.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import typing as t

import pyarrow as pa
from pyiceberg.expressions import EqualTo

from mari_server.persistence.iceberg.warehouse import IcebergWarehouse, warehouse
from mari_components.documents import DocumentVersion, document_key


TABLE = "mari.knowledge_versions"
SCHEMA = pa.schema([
    pa.field("version_id", pa.string(), nullable=False),
    pa.field("project_id", pa.int64(), nullable=False),
    pa.field("document_key", pa.string(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("external_id", pa.string(), nullable=False),
    pa.field("revision", pa.string(), nullable=False),
    pa.field("title", pa.string(), nullable=False),
    pa.field("body", pa.string(), nullable=False),
    pa.field("content_hash", pa.string(), nullable=False),
    pa.field("status", pa.string(), nullable=False),
    pa.field("source_url", pa.string(), nullable=False),
    pa.field("acl_json", pa.string(), nullable=False),
    pa.field("reason", pa.string(), nullable=False),
    pa.field("actor", pa.string(), nullable=False),
    pa.field("recorded_at", pa.timestamp("us", tz="UTC"), nullable=False),
])


class IcebergDocumentStore:
    def __init__(self, store: IcebergWarehouse | None = None):
        self.store = store or warehouse()
        self._lock = threading.RLock()
        try:
            self.store.catalog.load_table(TABLE)
        except Exception:
            self.store.catalog.create_table(TABLE, schema=SCHEMA)

    def _rows(self, *, key: str | None = None, project_id: int | None = None) -> list[dict[str, t.Any]]:
        table = self.store.catalog.load_table(TABLE)
        if key is not None:
            scan = table.scan(row_filter=EqualTo("document_key", key))
        elif project_id is not None:
            scan = table.scan(row_filter=EqualTo("project_id", project_id))
        else:
            scan = table.scan()
        return scan.to_arrow().to_pylist()

    @staticmethod
    def _latest(rows: list[dict[str, t.Any]]) -> dict[str, dict[str, t.Any]]:
        latest: dict[str, dict[str, t.Any]] = {}
        for row in rows:
            prior = latest.get(row["document_key"])
            if prior is None or (row["recorded_at"], row["version_id"]) > (prior["recorded_at"], prior["version_id"]):
                latest[row["document_key"]] = row
        return latest

    def append(self, version: DocumentVersion) -> dict[str, t.Any]:
        key = document_key(version.project_id, version.source_id, version.external_id)
        content_hash = version.content_hash
        acl_json = version.acl_json
        with self._lock:
            latest = self._latest(self._rows(key=key)).get(key)
            # Replayed connector pages are idempotent. ACL and lifecycle changes
            # remain versions even when source content did not change.
            if latest and all((
                latest["revision"] == version.revision,
                latest["content_hash"] == content_hash,
                latest["title"] == version.title,
                latest["status"] == version.status,
                latest["source_url"] == version.source_url,
                latest["acl_json"] == acl_json,
            )):
                return latest
            row = {
                "version_id": version.version_id,
                "project_id": version.project_id,
                "document_key": key,
                "source_id": version.source_id,
                "external_id": version.external_id,
                "revision": version.revision,
                "title": version.title,
                "body": version.body,
                "content_hash": content_hash,
                "status": version.status,
                "source_url": version.source_url,
                "acl_json": acl_json,
                "reason": version.reason,
                "actor": version.actor,
                "recorded_at": version.recorded_at.astimezone(dt.timezone.utc),
            }
            self.store.catalog.load_table(TABLE).append(
                pa.Table.from_pylist([row], schema=SCHEMA),
                snapshot_properties={"mari.document-key": key, "mari.version-id": version.version_id},
            )
            return row

    def transition(self, *, project_id: int, source_id: str, external_id: str,
                   status: str, reason: str, actor: str) -> dict[str, t.Any]:
        current = self.get(project_id, source_id, external_id, include_deleted=True)
        if current is None:
            raise KeyError("document does not exist")
        return self.append(DocumentVersion(
            project_id=project_id, source_id=source_id, external_id=external_id,
            revision=current["revision"], title=current["title"], body=current["body"],
            status=status, source_url=current["source_url"],
            acl=json.loads(current["acl_json"]), reason=reason, actor=actor,
        ))

    def get(self, project_id: int, source_id: str, external_id: str,
            *, include_deleted: bool = False) -> dict[str, t.Any] | None:
        key = document_key(project_id, source_id, external_id)
        row = self._latest(self._rows(key=key)).get(key)
        return row if row and (include_deleted or row["status"] != "deleted") else None

    def current(self, project_id: int, *, include_archived: bool = False) -> list[dict[str, t.Any]]:
        rows = list(self._latest(self._rows(project_id=project_id)).values())
        allowed = {"active", "archived"} if include_archived else {"active"}
        return sorted((row for row in rows if row["status"] in allowed), key=lambda row: row["title"].lower())

    def history(self, project_id: int, source_id: str, external_id: str) -> list[dict[str, t.Any]]:
        key = document_key(project_id, source_id, external_id)
        rows = [row for row in self._rows(key=key) if row["project_id"] == project_id]
        return sorted(rows, key=lambda row: (row["recorded_at"], row["version_id"]))

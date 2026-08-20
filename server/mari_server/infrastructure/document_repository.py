"""Iceberg canonical and Postgres projection adapters for document use cases."""

from __future__ import annotations

import json
import threading

from mari_server.application.excerpt import excerpt
from mari_server.application.documents import DocumentPorts, ProjectionFields
from mari_server.domain.documents import DocumentVersion
from mari_server.infrastructure.document_store import IcebergDocumentStore


_STORE: IcebergDocumentStore | None = None
_STORE_LOCK = threading.Lock()


def canonical_store() -> IcebergDocumentStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = IcebergDocumentStore()
        return _STORE


def _acl(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def ports(conn) -> DocumentPorts:
    store = canonical_store()

    def upsert_projection(version: DocumentVersion, fields: ProjectionFields) -> tuple[int, bool]:
        row = conn.execute(
            """INSERT INTO documents
                 (project_id, source, external_id, title, snippet, body, author, author_initials,
                  kind, updated_src, created_src, content_hash, source_path, source_id,
                  acl_visibility, acl_principals)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, CURRENT_DATE,
                       %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, source, external_id) DO UPDATE SET
                 title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
                 author = EXCLUDED.author, kind = EXCLUDED.kind, updated_src = CURRENT_DATE,
                 content_hash = EXCLUDED.content_hash, source_path = EXCLUDED.source_path,
                 source_id = EXCLUDED.source_id, acl_visibility = EXCLUDED.acl_visibility,
                 acl_principals = EXCLUDED.acl_principals
               RETURNING id, (xmax = 0) AS inserted""",
            (version.project_id, fields.source, version.external_id, version.title,
             excerpt(version.body, version.title), version.body, fields.author,
             fields.author_initials, fields.kind, version.revision, version.source_url,
             int(version.source_id), str(version.acl.get("visibility") or "project"),
             json.dumps(version.acl.get("principals") or [])),
        ).fetchone()
        return int(row["id"]), bool(row["inserted"])

    def projected_versions(project_id: int, document_ids: list[int]) -> list[DocumentVersion]:
        rows = conn.execute(
            """SELECT source_id, external_id, content_hash, title, body, source_path,
                      acl_visibility, acl_principals
                 FROM documents WHERE project_id = %s AND id = ANY(%s)""",
            (project_id, document_ids),
        ).fetchall()
        return [DocumentVersion(
            project_id=project_id, source_id=str(row["source_id"]),
            external_id=str(row["external_id"]), revision=str(row["content_hash"] or ""),
            title=str(row["title"]), body=str(row["body"] or ""),
            source_url=str(row["source_path"] or ""),
            acl={"visibility": str(row["acl_visibility"] or "project"),
                 "principals": _acl(row["acl_principals"])},
        ) for row in rows]

    def delete_projections(project_id: int, document_ids: list[int]) -> None:
        for table, column in (
            ("tags", "document_id"), ("findings", "document_id"),
            ("changes", "document_id"), ("watches", "document_id"),
        ):
            conn.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s)", (document_ids,))
        conn.execute("DELETE FROM edges WHERE from_doc = ANY(%s) OR to_doc = ANY(%s)",
                     (document_ids, document_ids))
        conn.execute("DELETE FROM documents WHERE project_id = %s AND id = ANY(%s)",
                     (project_id, document_ids))

    return DocumentPorts(
        append_canonical=lambda version: store.append(version),
        delete_canonical=lambda version: store.append(version),
        upsert_projection=upsert_projection,
        projected_versions=projected_versions,
        delete_projections=delete_projections,
    )

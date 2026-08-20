"""Canonical Iceberg documents with a transactional Postgres query projection."""

from __future__ import annotations

import json
import threading

import access
from excerpt import excerpt
from mari_server.infrastructure.document_store import IcebergDocumentStore, KnowledgeVersion


_STORE: IcebergDocumentStore | None = None
_STORE_LOCK = threading.Lock()


def canonical_store() -> IcebergDocumentStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = IcebergDocumentStore()
        return _STORE


def upsert(
    conn, *, source_id: int, external_id: str, title: str, body: str,
    source_path: str, kind: str, revision: str, author: str,
    source: str, initials: str, acl_visibility: str,
    acl_principals: tuple[str, ...],
) -> tuple[int, bool]:
    """Append canonical content, then update its Postgres search projection.

    Iceberg is written first. If the SQL transaction fails, replay is safe and
    idempotent; the provider checkpoint cannot advance past the failed SQL page.
    """
    project_id = access.require_current_access().project_id
    canonical_store().append(KnowledgeVersion(
        project_id=project_id,
        source_id=str(source_id),
        external_id=external_id,
        revision=revision,
        title=title,
        body=body,
        source_url=source_path,
        acl={"visibility": acl_visibility, "principals": list(acl_principals)},
        reason="connector ingestion",
        actor=author or "connector",
    ))
    snippet = excerpt(body, title)
    row = conn.execute("""
        INSERT INTO documents (project_id, source, external_id, title, snippet, body, author, author_initials,
                               kind, updated_src, created_src, content_hash, source_path, source_id,
                               acl_visibility, acl_principals)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, CURRENT_DATE, %s, %s, %s, %s, %s)
        ON CONFLICT (project_id, source, external_id) DO UPDATE SET
          title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
          author = EXCLUDED.author, kind = EXCLUDED.kind, updated_src = CURRENT_DATE,
          content_hash = EXCLUDED.content_hash, source_path = EXCLUDED.source_path,
          source_id = EXCLUDED.source_id, acl_visibility = EXCLUDED.acl_visibility,
          acl_principals = EXCLUDED.acl_principals
        RETURNING id, (xmax = 0) AS inserted""",
        (project_id, source, external_id, title, snippet, body, author, initials, kind,
         revision, source_path, source_id, acl_visibility, json.dumps(list(acl_principals))),
    ).fetchone()
    return int(row["id"]), bool(row["inserted"])


def delete(conn, document_ids: list[int], *, reason: str = "provider tombstone") -> None:
    if not document_ids:
        return
    project_id = access.require_current_access().project_id
    rows = conn.execute(
        """SELECT source_id, external_id FROM documents
             WHERE project_id = %s AND id = ANY(%s)""",
        (project_id, document_ids),
    ).fetchall()
    store = canonical_store()
    for row in rows:
        store.transition(
            project_id=project_id, source_id=str(row["source_id"]),
            external_id=str(row["external_id"]), status="deleted",
            reason=reason, actor="connector",
        )
    for table, column in (
        ("tags", "document_id"), ("findings", "document_id"),
        ("changes", "document_id"), ("watches", "document_id"),
    ):
        conn.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s)", (document_ids,))
    conn.execute(
        "DELETE FROM edges WHERE from_doc = ANY(%s) OR to_doc = ANY(%s)",
        (document_ids, document_ids),
    )
    conn.execute("DELETE FROM documents WHERE project_id = %s AND id = ANY(%s)",
                 (project_id, document_ids))

"""Iceberg canonical and Postgres projection adapters for document use cases."""

from __future__ import annotations

import json
import threading

from mari_server.persistence.postgres import connection as db
from mari_server.identity import context as access
from mari_components.knowledge.excerpt import excerpt
from mari_components.knowledge.lifecycle import DocumentPorts, ProjectionFields
from mari_components.documents import DocumentVersion
from mari_server.persistence.iceberg.documents import IcebergDocumentStore


_STORE: IcebergDocumentStore | None = None
_STORE_LOCK = threading.Lock()


_DOCUMENT_SELECT = """SELECT d.id, d.source, d.external_id, d.title, d.snippet,
       d.body, d.author, d.author_initials, d.updated_src, d.kind,
       array_remove(array_agg(t.tag), NULL) AS tags
  FROM documents d LEFT JOIN tags t ON t.document_id = d.id AND t.project_id = d.project_id"""


def recent(limit: int, offset: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            _DOCUMENT_SELECT + """ WHERE d.project_id = %s GROUP BY d.id
              ORDER BY d.updated_src DESC NULLS LAST, d.id DESC LIMIT %s OFFSET %s""",
            (project_id, limit, offset),
        ).fetchall()


def count() -> int:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM documents WHERE project_id = %s", (project_id,),
        ).fetchone()
    return int(row["n"])


def recent_searches(limit: int) -> list[str]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT detail, max(at) AS last FROM usage_log
                 WHERE project_id = %s AND kind = 'search' AND detail <> ''
                 GROUP BY detail ORDER BY last DESC LIMIT %s""", (project_id, limit),
        ).fetchall()
    return [str(row["detail"]) for row in rows]


def record_search(query: str, *, window: str = "5 minutes") -> None:
    project_id = access.require_current_access().project_id
    detail = query[:120]
    with db.connect() as conn:
        conn.execute(f"""INSERT INTO usage_log (project_id, kind, detail)
          SELECT %s, 'search', %s
           WHERE NOT EXISTS (SELECT 1 FROM usage_log
                              WHERE project_id = %s AND kind = 'search' AND detail = %s
                                AND at > now() - interval '{window}')""",
                     (project_id, detail, project_id, detail))


def related(document_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT d.id, d.source, d.title, e.rel, 'out' AS direction
                 FROM edges e JOIN documents d ON d.id = e.to_doc
                WHERE e.project_id = %s AND d.project_id = %s AND e.from_doc = %s
               UNION ALL
               SELECT d.id, d.source, d.title, e.rel, 'in' AS direction
                 FROM edges e JOIN documents d ON d.id = e.from_doc
                WHERE e.project_id = %s AND d.project_id = %s AND e.to_doc = %s
               ORDER BY title, id, rel""",
            (project_id, project_id, document_id, project_id, project_id, document_id),
        ).fetchall()


def get(document_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        rows = conn.execute(
            _DOCUMENT_SELECT + " WHERE d.project_id = %s AND d.id = %s GROUP BY d.id",
            (project_id, document_id),
        ).fetchall()
    return rows[0] if rows else None


def is_watched(document_id: int, user_name: str) -> bool:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return bool(conn.execute(
            """SELECT 1 FROM watches
                 WHERE project_id = %s AND user_name = %s AND document_id = %s""",
            (project_id, user_name, document_id),
        ).fetchone())


def revisions(document_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT e.* FROM events e JOIN documents d ON e.target = d.title
                 WHERE d.project_id = %s AND e.project_id = %s AND d.id = %s
                 ORDER BY e.occurred_at DESC LIMIT 20""",
            (project_id, project_id, document_id),
        ).fetchall()


def findings(document_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT f.* FROM findings f JOIN documents d ON d.id = f.document_id
                 WHERE f.project_id = %s AND d.project_id = %s AND f.document_id = %s
                 ORDER BY f.id""", (project_id, project_id, document_id),
        ).fetchall()


def changes(document_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT c.* FROM changes c JOIN documents d ON d.id = c.document_id
                 WHERE c.project_id = %s AND d.project_id = %s AND c.document_id = %s
                 ORDER BY c.id""", (project_id, project_id, document_id),
        ).fetchall()


def history(document_id: int) -> list[dict]:
    project_id = access.require_current_access().project_id
    with db.connect() as conn:
        return conn.execute(
            """SELECT e.actor, e.verb, e.target,
                      to_char(e.occurred_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS at
                 FROM events e JOIN documents d ON e.target = d.title
                WHERE e.project_id = %s AND d.project_id = %s AND d.id = %s
                ORDER BY e.occurred_at DESC LIMIT 30""",
            (project_id, project_id, document_id),
        ).fetchall()


def ids_for_source_path(conn, project_id: int, source_id: int, source_path: str) -> list[int]:
    return [int(row["id"]) for row in conn.execute(
        "SELECT id FROM documents WHERE project_id = %s AND source_id = %s AND source_path = %s",
        (project_id, source_id, source_path)).fetchall()]


def clear_derived_content(conn, document_id: int) -> None:
    conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
    conn.execute("UPDATE documents SET embedding = NULL WHERE id = %s", (document_id,))


def source_document_paths(conn, source_id: int) -> list[dict]:
    return conn.execute("SELECT id, source_path FROM documents WHERE source_id = %s", (source_id,)).fetchall()


def finalize_source(conn, project_id: int, source_id: int, config: dict) -> int:
    rows = conn.execute("SELECT count(*) AS n FROM documents WHERE project_id = %s AND source_id = %s",
                        (project_id, source_id)).fetchone()["n"]
    conn.execute("""UPDATE sources SET config = %s, last_sync_at = now(), docs_count = %s,
      stat_num = %s, stat_unit = 'docs', health = 'Healthy', status = 'active'
      WHERE id = %s AND project_id = %s""", (json.dumps(config), rows, str(rows), source_id, project_id))
    return int(rows)


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
                  acl_visibility, acl_principals, observed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s)
               ON CONFLICT (project_id, source, external_id) DO UPDATE SET
                 title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
                 author = EXCLUDED.author, kind = EXCLUDED.kind,
                 updated_src = EXCLUDED.updated_src, observed_at = EXCLUDED.observed_at,
                 content_hash = EXCLUDED.content_hash, source_path = EXCLUDED.source_path,
                 source_id = EXCLUDED.source_id, acl_visibility = EXCLUDED.acl_visibility,
                 acl_principals = EXCLUDED.acl_principals
               RETURNING id, (xmax = 0) AS inserted""",
            (version.project_id, fields.source, version.external_id, version.title,
             excerpt(version.body, version.title), version.body, fields.author,
             fields.author_initials, fields.kind, version.source_updated_at,
             version.source_updated_at, version.revision, version.source_url,
             int(version.source_id), str(version.acl.get("visibility") or "project"),
             json.dumps(version.acl.get("principals") or []), version.recorded_at),
        ).fetchone()
        return int(row["id"]), bool(row["inserted"])

    def projected_versions(project_id: int, document_ids: list[int]) -> list[DocumentVersion]:
        rows = conn.execute(
            """SELECT source_id, external_id, content_hash, title, body, source_path, updated_src,
                      acl_visibility, acl_principals
                 FROM documents WHERE project_id = %s AND id = ANY(%s)""",
            (project_id, document_ids),
        ).fetchall()
        return [DocumentVersion(
            project_id=project_id, source_id=str(row["source_id"]),
            external_id=str(row["external_id"]), revision=str(row["content_hash"] or ""),
            title=str(row["title"]), body=str(row["body"] or ""),
            source_url=str(row["source_path"] or ""),
            source_updated_at=row["updated_src"],
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
        append_canonical_many=store.append_many,
        delete_canonical=lambda version: store.append(version),
        upsert_projection=upsert_projection,
        projected_versions=projected_versions,
        delete_projections=delete_projections,
    )

"""Canonical document projection and derived embedding maintenance."""

from __future__ import annotations

import json
import statistics
from mari_server.providers import models as llm
from mari_server.providers import vectors as retrieval
from mari_server.identity import context as access
from mari_components.knowledge import lifecycle as document_application
from mari_components.documents import DocumentVersion
from mari_components.retrieval import chunk_text, content_hash, title_from_markdown
from mari_server.persistence.postgres import documents as document_repository
from mari_server.persistence.postgres import connection as postgres

def connection():
    return postgres.connect()


# ————————————————— chunking —————————————————


def chunk_settings() -> tuple[int, int]:
    with connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'chunking'").fetchone()
    cfg = (row["value"] if row else {}) or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    d = cfg.get("default", {})
    return int(d.get("max_tokens", 512)), int(d.get("overlap", 64))


def title_of(text: str, fallback: str) -> str:
    return title_from_markdown(text, fallback)


# ————————————————— document + chunk upserts —————————————————


def upsert_document(conn, source_id: int, external_id: str, title: str, body: str,
                     source_path: str, kind: str, content_hash: str, author: str,
                     source: str = "github", initials: str = "GH",
                     acl_visibility: str = "project",
                     acl_principals: tuple[str, ...] = ()) -> tuple[int, bool]:
    """Upsert one document. Returns (doc_id, inserted) — inserted is True for a
    brand-new row, False for an update (xmax = 0 only on fresh inserts).
    `source`/`initials` default to github; connect_sync passes the provider key."""
    project_id = access.require_current_access().project_id
    version = DocumentVersion(
        project_id=project_id, source_id=str(source_id), external_id=external_id,
        revision=content_hash, title=title, body=body, source_url=source_path,
        acl={"visibility": acl_visibility, "principals": list(acl_principals)},
        reason="connector ingestion", actor=author or "connector",
    )
    doc_id, inserted = document_application.upsert(
        version,
        document_application.ProjectionFields(
            source=source, kind=kind, author=author, author_initials=initials,
        ),
        ports=document_repository.ports(conn),
    )
    conn.commit()
    # Import lazily: queries imports ingest for status resolvers.
    from mari_server.search.service import invalidate_search
    invalidate_search(project_id)
    return doc_id, inserted


def sync_chunks(conn, doc_id: int, title: str, body: str,
                 max_tokens: int, overlap: int) -> tuple[int, int]:
    """Chunk + hash + embed-only-changed. Returns (chunks, newly_embedded)."""
    pieces = chunk_text(f"{title}\n\n{body}", max_tokens, overlap)
    project_id = access.require_current_access().project_id
    existing = {r["idx"]: r["content_hash"] for r in conn.execute(
        "SELECT idx, content_hash FROM chunks WHERE project_id = %s AND document_id = %s",
        (project_id, doc_id)).fetchall()}
    embedded = 0
    for idx, piece in enumerate(pieces):
        h = content_hash(piece)
        if existing.get(idx) == h:
            continue
        vec = llm.embed(piece)
        if vec:
            embedded += 1
        conn.execute("""
            INSERT INTO chunks (project_id, document_id, idx, content, content_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (document_id, idx) DO UPDATE SET
              content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
              embedding = EXCLUDED.embedding""",
            (project_id, doc_id, idx, piece, h, str(vec) if vec else None))
    conn.execute("DELETE FROM chunks WHERE project_id = %s AND document_id = %s AND idx >= %s",
                 (project_id, doc_id, len(pieces)))
    # doc-level embedding = mean of chunk embeddings (keeps existing doc search working)
    vecs = [r["embedding"] for r in conn.execute(
        """SELECT embedding::text AS embedding FROM chunks
           WHERE project_id = %s AND document_id = %s AND embedding IS NOT NULL""",
        (project_id, doc_id)).fetchall()]
    if vecs:
        parsed = [json.loads(v) for v in vecs]
        mean = [statistics.fmean(col) for col in zip(*parsed)]
        conn.execute("""UPDATE documents SET embedding = %s::vector
                        WHERE id = %s AND project_id = %s""", (str(mean), doc_id, project_id))
    conn.commit()
    # Chunk vectors are derived and intentionally live outside canonical
    # storage. A burst of changed documents produces one atomic MUVERA /
    # PolarQuant snapshot instead of rewriting an index per document.
    if embedded:
        retrieval.schedule_rebuild()
    return len(pieces), embedded


def delete_documents(conn, doc_ids: list[int]) -> None:
    if not doc_ids:
        return
    document_application.delete(
        access.require_current_access().project_id, doc_ids,
        reason="provider tombstone", actor="connector",
        ports=document_repository.ports(conn),
    )
    conn.commit()
    from mari_server.search.service import invalidate_search
    invalidate_search(access.require_current_access().project_id)

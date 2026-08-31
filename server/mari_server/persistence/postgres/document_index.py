"""Canonical document projection and derived embedding maintenance."""

from __future__ import annotations

import json
import statistics
import datetime as dt
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
                     acl_principals: tuple[str, ...] = (),
                     source_updated_at: str = "") -> tuple[int, bool]:
    """Upsert one document. Returns (doc_id, inserted) — inserted is True for a
    brand-new row, False for an update (xmax = 0 only on fresh inserts).
    `source`/`initials` default to github; connect_sync passes the provider key."""
    project_id = access.require_current_access().project_id
    provider_time = None
    if source_updated_at:
        provider_time = dt.datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
        if provider_time.tzinfo is None:
            provider_time = provider_time.replace(tzinfo=dt.timezone.utc)
        provider_time = provider_time.astimezone(dt.timezone.utc)
    version = DocumentVersion(
        project_id=project_id, source_id=str(source_id), external_id=external_id,
        revision=content_hash, title=title, body=body, source_url=source_path,
        acl={"visibility": acl_visibility, "principals": list(acl_principals)},
        reason="connector ingestion", actor=author or "connector",
        source_updated_at=provider_time,
    )
    doc_id, inserted = document_application.upsert(
        version,
        document_application.ProjectionFields(
            source=source, kind=kind, author=author, author_initials=initials,
        ),
        ports=document_repository.ports(conn),
    )
    conn.commit()
    return doc_id, inserted


def upsert_documents(conn, documents: list[dict]) -> list[tuple[int, bool]]:
    """Write one connector page through the canonical/projection boundary."""
    project_id = access.require_current_access().project_id
    prepared: list[tuple[DocumentVersion, document_application.ProjectionFields]] = []
    for document in documents:
        source_updated_at = document.get("source_updated_at") or ""
        provider_time = None
        if source_updated_at:
            provider_time = dt.datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
            if provider_time.tzinfo is None:
                provider_time = provider_time.replace(tzinfo=dt.timezone.utc)
            provider_time = provider_time.astimezone(dt.timezone.utc)
        prepared.append((DocumentVersion(
            project_id=project_id, source_id=str(document["source_id"]),
            external_id=document["external_id"], revision=document["content_hash"],
            title=document["title"], body=document["body"],
            source_url=document["source_path"],
            acl={"visibility": document["acl_visibility"],
                 "principals": list(document["acl_principals"])},
            reason="connector ingestion", actor=document["author"] or "connector",
            source_updated_at=provider_time,
        ), document_application.ProjectionFields(
            source=document["source"], kind="page", author=document["author"],
            author_initials=document["initials"],
        )))
    results = document_application.upsert_many(
        prepared, ports=document_repository.ports(conn),
    )
    conn.commit()
    return results


# A failed embed (provider down, model missing) must not erase the previous
# profile's vector for unchanged content: a profile rotation retries at every
# startup, and each failed pass used to null the legacy vector it still needed
# until a retry finally succeeded. The old vector only goes when the content
# itself changed, because then it describes text that no longer exists.
_CHUNK_UPSERT = """
    INSERT INTO chunks (project_id, document_id, idx, content, content_hash,
                        embedding_profile, embedding)
    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
    ON CONFLICT (document_id, idx) DO UPDATE SET
      content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
      embedding_profile = CASE
        WHEN EXCLUDED.embedding IS NOT NULL
          OR chunks.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        THEN EXCLUDED.embedding_profile ELSE chunks.embedding_profile END,
      embedding = CASE
        WHEN EXCLUDED.embedding IS NOT NULL
          OR chunks.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        THEN EXCLUDED.embedding ELSE chunks.embedding END
    RETURNING id"""


def sync_chunks(conn, doc_id: int, title: str, body: str,
                 max_tokens: int, overlap: int) -> tuple[int, int]:
    """Chunk + hash + embed-only-changed. Returns (chunks, newly_embedded)."""
    pieces = chunk_text(f"{title}\n\n{body}", max_tokens, overlap)
    project_id = access.require_current_access().project_id
    profile = llm.embedding_profile()
    provider, model = llm.embedding_model()
    existing = {r["idx"]: r for r in conn.execute(
        """SELECT c.id, c.idx, c.content_hash,
                  EXISTS (SELECT 1 FROM chunk_embeddings e
                           WHERE e.chunk_id = c.id AND e.project_id = c.project_id
                             AND e.embedding_profile = %s AND e.purpose = 'document'
                             AND e.content_hash = c.content_hash) AS embedded
             FROM chunks c WHERE c.project_id = %s AND c.document_id = %s""",
        (profile, project_id, doc_id)).fetchall()}
    embedded = 0
    for idx, piece in enumerate(pieces):
        h = content_hash(piece)
        prior = existing.get(idx)
        if prior and prior["content_hash"] == h and prior["embedded"]:
            continue
        vec = llm.embed(piece, purpose="document")
        if vec:
            embedded += 1
        chunk = conn.execute(
            _CHUNK_UPSERT,
            (project_id, doc_id, idx, piece, h, profile, str(vec) if vec else None)).fetchone()
        if vec:
            _store_embedding(
                conn, project_id, int(chunk["id"]), doc_id, profile,
                provider, model, h, vec,
            )
    conn.execute("DELETE FROM chunks WHERE project_id = %s AND document_id = %s AND idx >= %s",
                 (project_id, doc_id, len(pieces)))
    _update_document_mean(conn, project_id, doc_id, profile)
    conn.commit()
    # PolarQuant/MUVERA is disposable acceleration state. The ordered chunk
    # vectors needed to reconstruct it remain versioned in Postgres.
    if embedded:
        retrieval.schedule_rebuild()
    return len(pieces), embedded


def sync_chunks_many(conn, documents: list[tuple[int, str, str]],
                     max_tokens: int, overlap: int) -> tuple[int, int]:
    """Synchronize derived chunks for several documents with one model batch."""
    project_id = access.require_current_access().project_id
    profile = llm.embedding_profile()
    provider, model = llm.embedding_model()
    prepared: list[tuple[int, int, str, str]] = []
    piece_counts: dict[int, int] = {}
    for doc_id, title, body in documents:
        pieces = chunk_text(f"{title}\n\n{body}", max_tokens, overlap)
        piece_counts[doc_id] = len(pieces)
        existing = {r["idx"]: r for r in conn.execute(
            """SELECT c.id, c.idx, c.content_hash,
                      EXISTS (SELECT 1 FROM chunk_embeddings e
                               WHERE e.chunk_id = c.id AND e.project_id = c.project_id
                                 AND e.embedding_profile = %s AND e.purpose = 'document'
                                 AND e.content_hash = c.content_hash) AS embedded
                 FROM chunks c WHERE c.project_id = %s AND c.document_id = %s""",
            (profile, project_id, doc_id)).fetchall()}
        for idx, piece in enumerate(pieces):
            h = content_hash(piece)
            prior = existing.get(idx)
            if not (prior and prior["content_hash"] == h and prior["embedded"]):
                prepared.append((doc_id, idx, piece, h))

    vectors = llm.embed_many(row[2] for row in prepared)
    embedded = 0
    for (doc_id, idx, piece, h), vec in zip(prepared, vectors, strict=True):
        if vec:
            embedded += 1
        chunk = conn.execute(
            _CHUNK_UPSERT,
            (project_id, doc_id, idx, piece, h, profile, str(vec) if vec else None)).fetchone()
        if vec:
            _store_embedding(
                conn, project_id, int(chunk["id"]), doc_id, profile,
                provider, model, h, vec,
            )

    for doc_id, count in piece_counts.items():
        conn.execute("DELETE FROM chunks WHERE project_id = %s AND document_id = %s AND idx >= %s",
                     (project_id, doc_id, count))
        _update_document_mean(conn, project_id, doc_id, profile)
    conn.commit()
    if embedded:
        retrieval.schedule_rebuild()
    return sum(piece_counts.values()), embedded


def reindex_all(batch_size: int = 100) -> tuple[int, int]:
    """Rebuild every projected document whose embedding profile is stale.

    Chunks whose content and profile already match are reused without an HTTP
    call. Batches commit independently because vectors are derived and a
    stopped run can safely resume from the cache boundary.
    """
    project_id = access.require_current_access().project_id
    max_tokens, overlap = chunk_settings()
    documents = 0
    embedded = 0
    offset = 0
    while True:
        with connection() as conn:
            rows = conn.execute(
                """SELECT id, title, body FROM documents
                     WHERE project_id = %s ORDER BY id LIMIT %s OFFSET %s""",
                (project_id, batch_size, offset),
            ).fetchall()
            if not rows:
                break
            _, changed = sync_chunks_many(
                conn,
                [(int(row["id"]), str(row["title"]), str(row["body"] or "")) for row in rows],
                max_tokens, overlap,
            )
        documents += len(rows)
        embedded += changed
        offset += len(rows)
    return documents, embedded


def needs_reindex() -> bool:
    """Whether any projected chunk is missing the active embedding profile."""
    project_id = access.require_current_access().project_id
    profile = llm.embedding_profile()
    with connection() as conn:
        row = conn.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM chunks c
                    WHERE c.project_id = %s
                      AND NOT EXISTS (
                        SELECT 1 FROM chunk_embeddings e
                         WHERE e.project_id = c.project_id AND e.chunk_id = c.id
                           AND e.embedding_profile = %s AND e.purpose = 'document'
                           AND e.content_hash = c.content_hash
                      )
               ) AS stale""",
            (project_id, profile),
        ).fetchone()
    return bool(row and row["stale"])


def _store_embedding(conn, project_id: int, chunk_id: int, document_id: int,
                     profile: str, provider: str, model: str, content_hash_value: str,
                     vector: list[float]) -> None:
    """Append or refresh one profile without deleting vectors from another."""
    conn.execute(
        """INSERT INTO chunk_embeddings (
           project_id, chunk_id, document_id, embedding_profile, provider,
             model, purpose, representation, distance_metric, normalized,
             dimensions, content_hash, embedding
           ) VALUES (%s, %s, %s, %s, %s, %s, 'document',
                     'dense-chunk-set-v1', 'cosine-maxsim', false, %s, %s, %s::vector)
           ON CONFLICT (chunk_id, embedding_profile, purpose) DO UPDATE SET
             project_id = EXCLUDED.project_id,
             document_id = EXCLUDED.document_id,
             provider = EXCLUDED.provider,
             model = EXCLUDED.model,
             dimensions = EXCLUDED.dimensions,
             content_hash = EXCLUDED.content_hash,
             embedding = EXCLUDED.embedding,
             updated_at = now()""",
        (project_id, chunk_id, document_id, profile, provider, model,
         len(vector), content_hash_value, str(vector)),
    )


def _update_document_mean(conn, project_id: int, document_id: int, profile: str) -> None:
    """Maintain the legacy document centroid; multi-vector retrieval never reads it."""
    vecs = [row["embedding"] for row in conn.execute(
        """SELECT e.embedding::text AS embedding
             FROM chunks c JOIN chunk_embeddings e
               ON e.project_id = c.project_id AND e.chunk_id = c.id
            WHERE c.project_id = %s AND c.document_id = %s
              AND e.embedding_profile = %s AND e.purpose = 'document'
              AND e.content_hash = c.content_hash
            ORDER BY c.idx""",
        (project_id, document_id, profile)).fetchall()]
    if not vecs:
        return
    parsed = [json.loads(value) for value in vecs]
    mean = [statistics.fmean(column) for column in zip(*parsed)]
    conn.execute(
        "UPDATE documents SET embedding = %s::vector WHERE id = %s AND project_id = %s",
        (str(mean), document_id, project_id),
    )


def delete_documents(conn, doc_ids: list[int]) -> None:
    if not doc_ids:
        return
    document_application.delete(
        access.require_current_access().project_id, doc_ids,
        reason="provider tombstone", actor="connector",
        ports=document_repository.ports(conn),
    )
    conn.commit()

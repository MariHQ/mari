"""Vector projection persistence."""

from mari_server.persistence.postgres import connection as db


def embedded_chunks(project_id: int, embedding_profile: str) -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""
          SELECT c.document_id, c.id AS chunk_id, c.idx, c.content_hash,
                 e.provider, e.model, e.purpose, e.representation,
                 e.distance_metric, e.normalized, e.dimensions,
                 e.embedding::text AS embedding
            FROM chunks c
            JOIN chunk_embeddings e
              ON e.project_id = c.project_id AND e.chunk_id = c.id
             AND e.content_hash = c.content_hash
           WHERE c.project_id = %s AND e.embedding_profile = %s
             AND e.purpose = 'document'
           ORDER BY c.document_id, c.idx, c.id""",
          (project_id, embedding_profile)).fetchall()

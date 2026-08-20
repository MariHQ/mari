"""Vector projection persistence."""

from mari_server import db


def embedded_chunks(project_id: int) -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""SELECT document_id, content_hash, embedding::text AS embedding
          FROM chunks WHERE project_id = %s AND embedding IS NOT NULL ORDER BY document_id, idx""",
          (project_id,)).fetchall()

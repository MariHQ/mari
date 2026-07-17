"""Backfill document embeddings via ollama nomic-embed-text (DESIGN.md §5.2).

Run: python backfill.py   — embeds every document with a NULL embedding.
Idempotent and resumable (per-row commit = the checkpointing seam).
"""

from __future__ import annotations

import os

import psycopg

import llm

DB_URL = os.environ.get("MARI_DB", "postgresql://localhost/mari_cloud")


def main() -> None:
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(
            "SELECT id, title, snippet, body FROM documents WHERE embedding IS NULL ORDER BY id"
        ).fetchall()
        print(f"{len(rows)} documents to embed")
        for doc_id, title, snippet, body in rows:
            vec = llm.embed(f"{title}\n{snippet}\n{body}")
            if vec is None:
                print(f"  doc {doc_id}: ollama unavailable, stopping")
                return
            conn.execute(
                "UPDATE documents SET embedding = %s::vector WHERE id = %s",
                (str(vec), doc_id),
            )
            conn.commit()
            print(f"  doc {doc_id}: embedded ({len(vec)} dims)")


if __name__ == "__main__":
    main()

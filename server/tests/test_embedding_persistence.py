from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from mari_server.persistence.postgres import document_index
from mari_server.providers import vectors as retrieval


class RecordingConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class VersionedEmbeddingPersistenceTests(unittest.TestCase):
    def test_failed_embed_never_erases_the_previous_profile_vector(self):
        # A profile rotation retries at every startup; a pass whose embed
        # failed must leave the legacy vector alone for unchanged content, or
        # keyword-only degradation becomes permanent vector loss. The vector
        # is only replaced when the model answered or the content changed.
        sql = " ".join(document_index._CHUNK_UPSERT.split())
        self.assertIn("embedding = CASE", sql)
        self.assertIn("embedding_profile = CASE", sql)
        self.assertIn("WHEN EXCLUDED.embedding IS NOT NULL", sql)
        self.assertIn("OR chunks.content_hash IS DISTINCT FROM EXCLUDED.content_hash", sql)
        self.assertIn("ELSE chunks.embedding END", sql)
        self.assertIn("ELSE chunks.embedding_profile END", sql)
        # content itself always follows the sync, vectors or not
        self.assertIn("content = EXCLUDED.content, content_hash = EXCLUDED.content_hash", sql)

    def test_profile_rotation_upserts_only_the_selected_chunk_profile(self):
        conn = RecordingConnection()

        document_index._store_embedding(
            conn, 7, 101, 22, "ollama:nomic:v2", "ollama", "nomic", "hash-a", [0.1, 0.2],
        )

        sql, args = conn.calls[0]
        self.assertIn("INSERT INTO chunk_embeddings", sql)
        self.assertIn("ON CONFLICT (chunk_id, embedding_profile, purpose)", sql)
        self.assertIn("'dense-chunk-set-v1', 'cosine-maxsim', false", sql)
        self.assertEqual(args[:6], (7, 101, 22, "ollama:nomic:v2", "ollama", "nomic"))
        self.assertEqual(args[6:8], (2, "hash-a"))

    def test_polar_index_rebuild_keeps_every_ordered_chunk_vector(self):
        rows = [
            {"document_id": 22, "chunk_id": 101, "content_hash": "a", "embedding": "[1,0,0]"},
            {"document_id": 22, "chunk_id": 102, "content_hash": "b", "embedding": "[0,1,0]"},
            {"document_id": 23, "chunk_id": 103, "content_hash": "c", "embedding": "[0,0,1]"},
        ]
        index = Mock()
        index.build.return_value = {"documents": 2, "vectors": 3}
        with patch("mari_server.identity.context.require_current_access",
                   return_value=SimpleNamespace(project_id=7)), \
             patch("mari_server.providers.models.embedding_profile", return_value="profile-v2"), \
             patch("mari_server.persistence.postgres.vectors.embedded_chunks",
                   return_value=rows) as read, \
             patch.object(retrieval, "index_for", return_value=index):
            result = retrieval.rebuild_from_database()

        read.assert_called_once_with(7, "profile-v2")
        matrices, hashes = index.build.call_args.args
        np.testing.assert_array_equal(
            matrices[22], np.asarray([[1, 0, 0], [0, 1, 0]], dtype=np.float32),
        )
        self.assertEqual(hashes, {22: "101:a|102:b", 23: "103:c"})
        self.assertEqual(index.build.call_args.kwargs["embedding_profile"], "profile-v2")
        self.assertEqual(result, {"documents": 2, "vectors": 3})

    def test_profile_reader_rejects_vectors_for_previous_chunk_content(self):
        conn = RecordingConnection([{"document_id": 22, "chunk_id": 101}])
        from mari_server.persistence.postgres import vectors as vector_store
        with patch.object(vector_store.db, "connect", return_value=conn):
            rows = vector_store.embedded_chunks(7, "profile-v2")

        sql, args = conn.calls[0]
        self.assertIn("e.content_hash = c.content_hash", sql)
        self.assertIn("ORDER BY c.document_id, c.idx, c.id", sql)
        self.assertEqual(args, (7, "profile-v2"))
        self.assertEqual(rows, [{"document_id": 22, "chunk_id": 101}])


if __name__ == "__main__":
    unittest.main()

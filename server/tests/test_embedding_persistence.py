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


class ScriptedConnection:
    """Routes each statement to a scripted result by an SQL fragment, so one
    connection can serve a whole sync_chunks_many call. Records normalized SQL."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, tuple]] = []
        self._current = None
        self.commits = 0

    def execute(self, sql, args=()):
        flat = " ".join(sql.split())
        self.calls.append((flat, args))
        self._current = None
        for fragment, result in self.routes.items():
            if fragment in flat:
                self._current = result(flat, args) if callable(result) else result
                break
        return self

    def fetchone(self):
        return self._current

    def fetchall(self):
        return self._current or []

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ReindexWhenTheEmbedderIsDownTests(unittest.TestCase):
    """Item 3: a boot reindex with the embedding provider down must not
    rewrite every chunk row, and must stop and say so rather than loop."""

    def _sync(self, *, existing_hash, vector):
        from mari_components.retrieval import chunk_text, content_hash
        title, body = "Runbook", "How to restart the service."
        pieces = chunk_text(f"{title}\n\n{body}", 512, 64)
        self.assertEqual(len(pieces), 1)
        existing = [{"id": 101, "idx": 0,
                     "content_hash": existing_hash or content_hash(pieces[0]),
                     "embedded": False}]
        conn = ScriptedConnection({
            "FROM chunks c WHERE c.project_id": existing,
            "INSERT INTO chunks": {"id": 101},
            "JOIN chunk_embeddings e": [],
        })
        with patch("mari_server.identity.context.require_current_access",
                   return_value=SimpleNamespace(project_id=7)), \
             patch("mari_server.providers.models.embedding_profile", return_value="profile-v2"), \
             patch("mari_server.providers.models.embedding_model", return_value=("openai", "m")), \
             patch("mari_server.providers.models.embed_many", return_value=[vector]), \
             patch.object(retrieval, "schedule_rebuild"):
            result = document_index.sync_chunks_many(conn, [(22, title, body)], 512, 64)
        return result, conn

    def test_unchanged_chunk_without_a_vector_is_not_rewritten(self):
        result, conn = self._sync(existing_hash=None, vector=None)
        self.assertEqual(result, (1, 0))
        self.assertFalse(any("INSERT INTO chunks" in sql for sql, _ in conn.calls))
        self.assertFalse(any("INSERT INTO chunk_embeddings" in sql for sql, _ in conn.calls))

    def test_changed_chunk_is_still_written_even_without_a_vector(self):
        # content itself always follows the sync, vectors or not
        result, conn = self._sync(existing_hash="stale-hash", vector=None)
        self.assertEqual(result, (1, 0))
        self.assertEqual(sum("INSERT INTO chunks" in sql for sql, _ in conn.calls), 1)

    def test_unchanged_chunk_with_a_vector_is_written_and_embedded(self):
        result, conn = self._sync(existing_hash=None, vector=[0.1, 0.2])
        self.assertEqual(result, (1, 1))
        self.assertEqual(sum("INSERT INTO chunks" in sql for sql, _ in conn.calls), 1)
        self.assertEqual(sum("INSERT INTO chunk_embeddings" in sql for sql, _ in conn.calls), 1)

    def _reindex(self, *, last_error):
        batches = iter([[{"id": 1, "title": "a", "body": "b"}], [{"id": 2, "title": "c", "body": "d"}], []])
        conn = ScriptedConnection({"FROM documents": lambda *_: next(batches)})
        with patch("mari_server.identity.context.require_current_access",
                   return_value=SimpleNamespace(project_id=7)), \
             patch.object(document_index, "connection", return_value=conn), \
             patch.object(document_index, "chunk_settings", return_value=(512, 64)), \
             patch.object(document_index, "sync_chunks_many", return_value=(1, 0)) as sync, \
             patch("mari_server.providers.models.last_error", return_value=last_error):
            result = document_index.reindex_all(batch_size=1)
        return result, sync, conn

    def test_reindex_stops_after_a_batch_the_provider_failed_and_marks_sources(self):
        result, sync, conn = self._reindex(last_error="ollama: connection refused")
        self.assertEqual(result, (1, 0))
        self.assertEqual(sync.call_count, 1)
        health = [(sql, args) for sql, args in conn.calls if "UPDATE sources SET health = 'Error'" in sql]
        self.assertEqual(len(health), 1)
        self.assertIn("AND health = 'Healthy'", health[0][0])
        self.assertEqual(health[0][1], (7,))
        events = [args for sql, args in conn.calls if "INSERT INTO sync_events" in sql]
        self.assertEqual(events, [(7, "ollama: connection refused")])
        self.assertGreaterEqual(conn.commits, 1)

    def test_reindex_walks_every_batch_while_the_provider_answers(self):
        result, sync, conn = self._reindex(last_error="")
        self.assertEqual(result, (2, 0))
        self.assertEqual(sync.call_count, 2)
        self.assertFalse(any("UPDATE sources" in sql for sql, _ in conn.calls))


if __name__ == "__main__":
    unittest.main()

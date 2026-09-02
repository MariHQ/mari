from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mari_server.persistence.postgres import connector_sync as connect_sync


class _FakeSyncConn:
    """Just enough connection for merge_config and sync_source: the source
    row, the FOR UPDATE status probe, the document count, and a log of every
    statement. Doubles as the context manager document_index.connection()
    hands out."""

    def __init__(self, *, status, count=0, source=None):
        self.status = status
        self.count = count
        self.source = source
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def commit(self):
        return None

    def execute(self, sql, args=()):
        normalized = " ".join(sql.split())
        self.executed.append((normalized, args))
        result = Mock()
        if normalized.startswith("SELECT * FROM sources"):
            result.fetchone.return_value = self.source
        elif normalized.startswith("SELECT status FROM sources"):
            result.fetchone.return_value = {"status": self.status} if self.status else None
        elif normalized.startswith("SELECT count(*) AS n"):
            result.fetchone.return_value = {"n": self.count}
        else:
            result.fetchone.return_value = None
            result.fetchall.return_value = []
        return result


class MergeConfigNullManifestTests(unittest.TestCase):
    """A row whose config->'item_hashes' is JSON null (a connector written
    before the manifest existed, or one cleared by hand) made the jsonb
    `- text[]` delete raise "cannot delete from scalar" on every merge."""

    def test_a_json_null_manifest_is_treated_as_empty(self) -> None:
        conn = _FakeSyncConn(status="active", count=1)
        connect_sync.merge_config(conn, 42, {"cursor": "c2"}, hashes={"p1": "h1"}, dropped=("p9",))
        sql, args = next((sql, args) for sql, args in conn.executed if sql.startswith("UPDATE sources"))
        # a JSON null in either place becomes '{}' before the `- text[]` delete
        self.assertIn(
            "COALESCE(NULLIF( COALESCE(%(updates)s::jsonb -> 'item_hashes', config -> 'item_hashes', "
            "'{}'::jsonb), 'null'::jsonb), '{}'::jsonb) - %(dropped)s::text[]",
            sql,
        )
        self.assertEqual(json.loads(args["hashes"]), {"p1": "h1"})
        self.assertEqual(args["dropped"], ["p9"])


class SyncSourceSnapshotStatsTests(unittest.TestCase):
    """The flow step records a full reconcile only when the pass says its
    snapshot was complete. A throttled pass returns without an error key and
    used to be recorded as if the reconcile had happened."""

    SRC = {"id": 42, "kind": "connector", "provider": "confluence", "display_name": "Confluence",
           "status": "active",
           "config": {"provider_key": "confluence", "api_token": "tok", "cursor": "cur-1",
                      "item_hashes": {}}}

    def _run(self, conn, poll_pages, *, full=False):
        from mari_components.connectors.protocol import ValidationResult

        definition = SimpleNamespace(name="Confluence",
                                     validate=lambda _cfg, http=None: ValidationResult(True))
        status = Mock()
        with patch.object(connect_sync.document_index, "connection", return_value=conn), \
             patch.object(connect_sync.document_index, "chunk_settings", return_value=(100, 10)), \
             patch.object(connect_sync.document_index, "upsert_documents", return_value=[]), \
             patch.object(connect_sync, "connector_definition", return_value=definition), \
             patch.object(connect_sync.connector_provider, "poll_pages", side_effect=poll_pages), \
             patch.object(connect_sync, "retry_sleep"), \
             patch.object(connect_sync.access, "require_current_access",
                          return_value=SimpleNamespace(project_id=1)):
            return connect_sync.sync_source(
                42, full, update_status=status, fire_document_triggers=lambda _ids, _kind: [],
                invalidate_search=Mock())

    def test_a_finished_pass_reports_its_snapshot_complete(self) -> None:
        from mari_components import PollPage

        def poll_pages(*_args, **_kwargs):
            yield PollPage(next_cursor="cur-1", next_checkpoint="ckpt-1", snapshot_complete=False)
            yield PollPage(next_cursor="cur-2", snapshot_complete=True)

        result = self._run(_FakeSyncConn(status="active", source=self.SRC), poll_pages, full=True)
        self.assertNotIn("error", result)
        self.assertTrue(result["snapshot_complete"])

    def test_a_page_limited_pass_reports_its_snapshot_incomplete(self) -> None:
        from mari_components import PollPage

        def poll_pages(*_args, **_kwargs):
            yield PollPage(next_cursor="cur-1", next_checkpoint="ckpt-1", snapshot_complete=False)

        result = self._run(_FakeSyncConn(status="active", source=self.SRC), poll_pages)
        self.assertNotIn("error", result)
        self.assertFalse(result["snapshot_complete"])

    def test_a_throttled_pass_carries_no_snapshot_claim(self) -> None:
        from mari_components.errors import RateLimitFailure

        def poll_pages(*_args, **_kwargs):
            raise RateLimitFailure("provider rate limit exceeded", retry_after=3)
            yield  # noqa: unreachable — makes this a generator like the real one

        result = self._run(_FakeSyncConn(status="active", source=self.SRC), poll_pages, full=True)
        self.assertNotIn("error", result)
        self.assertIn("throttled", result)
        self.assertNotIn("snapshot_complete", result)


if __name__ == "__main__":
    unittest.main()

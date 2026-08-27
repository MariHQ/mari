from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.persistence.postgres import connector_sync as connect_sync
from mari_server.sources import routes as connectors_api
from mari_components.connectors import CONNECTOR_CATALOG
from mari_components.types import KnowledgeDocument


class ConnectorContractTests(unittest.TestCase):
    def test_catalog_hides_upload_and_website(self) -> None:
        with patch.object(connectors_api, "_connected_map", return_value={}):
            keys = [item["key"] for item in connectors_api.catalog()]
        self.assertIn("github", keys)
        self.assertNotIn("upload", keys)
        self.assertNotIn("website", keys)
        self.assertEqual(keys[:4], ["github", "slack", "gdrive", "confluence"])

    def test_requested_poll_connectors_are_discoverable(self) -> None:
        for key in ("confluence", "slack", "gdrive"):
            definition = CONNECTOR_CATALOG[key]
            self.assertTrue(callable(definition.validate))
            self.assertTrue(callable(definition.poll))

    def test_connector_config_masks_credentials_and_drops_hash_checkpoint_map(self) -> None:
        cfg = {"provider_key": "gdrive", "access_token": "ya29.secret", "folder_id": "abc",
               "item_hashes": {"doc": "hash"}, "cursor": "2026-08-19"}
        safe = connect_sync.masked_config("gdrive:abc", cfg)
        self.assertEqual(safe["access_token"], connect_sync.MASK)
        self.assertEqual(safe["folder_id"], "abc")
        self.assertNotIn("item_hashes", safe)

    def test_document_author_reads_the_connector_supplied_person(self) -> None:
        document = KnowledgeDocument("123", "Title", "Body", metadata={"author": "Ana Ruiz"})
        self.assertEqual(connect_sync.document_author(document), "Ana Ruiz")

    def test_document_author_is_empty_rather_than_the_source_name(self) -> None:
        # No connector fields expose a real person here; the source's own
        # display name (e.g. "Confluence", "Jira") must never leak in as
        # the document's owner.
        document = KnowledgeDocument("123", "Title", "Body", metadata={"status": "Backlog"})
        self.assertEqual(connect_sync.document_author(document), "")


class SweepInputTests(unittest.TestCase):
    """cursor/checkpoint hygiene for sync_source. Getting these wrong is how
    a resync deleted live documents and how Jira sources wedged on a stale
    page token (2026-08-23 connector sweep, findings 2-4)."""

    CFG = {"cursor": "2026-05-09T22:24:06.157-0400", "checkpoint": '{"start":200}'}

    def test_an_incremental_sync_uses_both_stored_values(self) -> None:
        cursor, checkpoint, authoritative = connect_sync.sweep_inputs(dict(self.CFG), full=False)
        self.assertEqual(cursor, self.CFG["cursor"])
        self.assertEqual(checkpoint, self.CFG["checkpoint"])
        self.assertFalse(authoritative)

    def test_an_explicit_resync_drops_cursor_and_stale_checkpoint(self) -> None:
        # Resuming a stale checkpoint mid-sweep makes the authoritative
        # snapshot delete every document the skipped windows held.
        cursor, checkpoint, authoritative = connect_sync.sweep_inputs(dict(self.CFG), full=True)
        self.assertIsNone(cursor)
        self.assertIsNone(checkpoint)
        self.assertTrue(authoritative)

    def test_a_pending_full_snapshot_is_unfiltered_but_resumes_its_checkpoint(self) -> None:
        # A cursor-filtered listing treated as a complete snapshot tombstones
        # everything the filter excluded; the checkpoint must survive so a
        # page_limit sweep finishes instead of restarting forever.
        cfg = dict(self.CFG, full_snapshot_pending=True)
        cursor, checkpoint, authoritative = connect_sync.sweep_inputs(cfg, full=False)
        self.assertIsNone(cursor)
        self.assertEqual(checkpoint, self.CFG["checkpoint"])
        self.assertTrue(authoritative)


class ValidationFailureTests(unittest.TestCase):
    def test_transient_validation_failures_raise_retryable_exceptions(self) -> None:
        # A network blip during validate must classify transient so
        # call_with_retry retries it instead of failing the sync run.
        from mari_components.connectors.protocol import ErrorKind, ValidationResult, classify_error
        for kind, expected in (("transient", ErrorKind.TRANSIENT),
                               ("rate_limit", ErrorKind.RATE_LIMIT),
                               ("auth", ErrorKind.AUTH),
                               ("", ErrorKind.PERMANENT)):
            error = connect_sync.validation_failure(ValidationResult(False, "boom", kind=kind))
            self.assertEqual(classify_error(error), expected, kind)


class ReconnectPausedSourceTests(unittest.TestCase):
    """Disconnect pauses the source row, so connecting the same provider again
    must revive it — refusing left admins with no path back at all (no edit,
    no delete): the 2026-08-27 customer dead end."""

    def _add(self, existing):
        from types import SimpleNamespace
        from mari_server.persistence.postgres import sources as source_store

        conn = _FakeSourcesConn(existing)
        with patch.object(source_store.db, "connect", return_value=conn), \
             patch.object(source_store.access, "require_current_access",
                          return_value=SimpleNamespace(project_id=1)):
            result = source_store.add_connector(
                "confluence", "Confluence", {"space_key": "FERN", "cursor": ""})
        return result, conn

    def test_a_paused_row_is_revived_with_the_new_config(self) -> None:
        source_id, conn = self._add({"id": 42, "status": "paused"})
        self.assertEqual(source_id, 42)
        update = next(sql for sql, _ in conn.executed if sql.startswith("UPDATE sources"))
        self.assertIn("status = 'active'", update)
        event_args = next(args for sql, args in conn.executed
                          if sql.startswith("INSERT INTO sync_events"))
        self.assertEqual(event_args[2], "reconnected: Confluence")

    def test_an_active_row_still_refuses_a_duplicate(self) -> None:
        source_id, conn = self._add({"id": 42, "status": "active"})
        self.assertIsNone(source_id)
        self.assertFalse(any(sql.startswith(("UPDATE", "INSERT")) for sql, _ in conn.executed))

    def test_no_row_inserts_as_before(self) -> None:
        source_id, conn = self._add(None)
        self.assertEqual(source_id, 7)
        self.assertTrue(any(sql.startswith("INSERT INTO sources") for sql, _ in conn.executed))


class _FakeSourcesConn:
    """Just enough connection for add_connector: the duplicate probe answers
    with the configured row, an INSERT hands back id 7, everything is logged."""

    def __init__(self, existing):
        self.existing = existing
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def transaction(self):
        return self

    def execute(self, sql, args=()):
        normalized = " ".join(sql.split())
        self.executed.append((normalized, args))
        row = None
        if normalized.startswith("SELECT id, status FROM sources"):
            row = self.existing
        elif normalized.startswith("INSERT INTO sources"):
            row = {"id": 7}
        result = unittest.mock.Mock()
        result.fetchone.return_value = row
        return result


if __name__ == "__main__":
    unittest.main()

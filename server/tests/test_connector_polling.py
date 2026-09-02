from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.persistence.postgres import connector_sync as connect_sync
from mari_server.providers import connectors as connector_provider
from mari_server.sources import routes as connectors_api
from mari_components.connectors import CONNECTOR_CATALOG
from mari_components.types import KnowledgeDocument
from mari_server.identity import access as access_module


class ConnectorContractTests(unittest.TestCase):
    def test_confluence_gets_a_larger_bounded_sweep_budget(self) -> None:
        confluence = connector_provider.request("confluence", None, None, {})
        slack = connector_provider.request("slack", None, None, {})
        self.assertEqual(confluence.page_limit, 100)
        self.assertEqual(slack.page_limit, 20)

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


class MultiInstanceCatalogTests(unittest.TestCase):
    """Two Confluence sites are two source rows under `key:qualifier` provider
    columns; the catalog must surface both, not silently show only the newest."""

    ROWS = [
        {"id": 3, "kind": "connector", "provider": "confluence:rippling.atlassian.net",
         "display_name": "Confluence · rippling.atlassian.net",
         "config": {"provider_key": "confluence"}},
        {"id": 5, "kind": "connector", "provider": "github",
         "display_name": "GitHub", "config": {"provider_key": "github"}},
        {"id": 9, "kind": "connector", "provider": "confluence:legal.atlassian.net",
         "display_name": "Legal wiki", "config": {"provider_key": "confluence"}},
    ]

    def _catalog(self):
        with patch.object(connectors_api.source_store, "connector_sources",
                          return_value=self.ROWS):
            return {item["key"]: item for item in connectors_api.catalog()}

    def test_catalog_lists_every_instance_with_name_and_qualifier(self) -> None:
        entries = self._catalog()
        self.assertEqual(entries["confluence"]["instances"], [
            {"sourceId": 3, "name": "Confluence · rippling.atlassian.net",
             "qualifier": "rippling.atlassian.net"},
            {"sourceId": 9, "name": "Legal wiki", "qualifier": "legal.atlassian.net"},
        ])
        # an unqualified provider column means a single default instance
        self.assertEqual(entries["github"]["instances"],
                         [{"sourceId": 5, "name": "GitHub", "qualifier": ""}])

    def test_connected_and_source_id_keep_the_old_single_value_shape(self) -> None:
        # consoles built before instances existed read these two fields only
        entries = self._catalog()
        self.assertTrue(entries["confluence"]["connected"])
        self.assertEqual(entries["confluence"]["sourceId"], 9)  # newest row wins
        self.assertFalse(entries["slack"]["connected"])
        self.assertIsNone(entries["slack"]["sourceId"])
        self.assertEqual(entries["slack"]["instances"], [])


class ConnectNamedInstanceTests(unittest.TestCase):
    """connect gained an optional caller-chosen name so a second instance of a
    provider is tellable apart, and a refused duplicate now names the row that
    blocks instead of answering only prose."""

    CONFIG = {"site_url": "https://rippling.atlassian.net/",
              "email": "ana@rippling.com", "api_token": "tok"}

    def _connect(self, name="", add_result=17, blocking=None):
        body = connectors_api.ProviderIn(provider="confluence",
                                         config=dict(self.CONFIG), name=name)
        with patch.object(connectors_api, "validate",
                          return_value={"ok": True, "error": ""}), \
             patch.object(connectors_api.source_store, "add_connector",
                          return_value=add_result) as add, \
             patch.object(connectors_api.source_store, "connector_source_for",
                          return_value=blocking) as lookup, \
             patch.object(connectors_api, "audit"), \
             patch.object(connectors_api.admin_store, "adopt_frozen_documents", return_value=0), \
             patch.object(connectors_api.flowengine, "ensure_sync_flow"), \
             patch.object(connectors_api.ingest, "start_sync"):
            result = connectors_api.connect(body)
        return result, add, lookup

    def test_a_custom_name_replaces_the_derived_display(self) -> None:
        result, add, _ = self._connect(name="  Legal wiki  ")
        provider_col, display, _cfg = add.call_args[0]
        self.assertEqual(provider_col, "confluence:rippling.atlassian.net")
        self.assertEqual(display, "Legal wiki")  # stripped, replaces the derived name
        self.assertEqual(result, {"sourceId": 17})

    def test_a_long_custom_name_is_capped_at_80_characters(self) -> None:
        _, add, _ = self._connect(name="w" * 200)
        self.assertEqual(add.call_args[0][1], "w" * 80)

    def test_an_empty_name_keeps_the_derived_display(self) -> None:
        _, add, _ = self._connect(name="   ")
        self.assertEqual(add.call_args[0][1], "Confluence · rippling.atlassian.net")

    def test_a_refused_duplicate_names_the_blocking_row(self) -> None:
        result, _, lookup = self._connect(
            add_result=None,
            blocking={"id": 9, "display_name": "Legal wiki", "status": "active"})
        lookup.assert_called_once_with("confluence:rippling.atlassian.net")
        self.assertEqual(result, {
            "error": "Confluence · rippling.atlassian.net is already connected",
            "existing": {"sourceId": 9, "name": "Legal wiki"},
        })

    def test_a_refusal_without_a_findable_row_stays_prose_only(self) -> None:
        # the blocking row vanished between refusal and lookup (a race with
        # removeSource) — the old prose-only answer is still an honest one
        result, _, _ = self._connect(add_result=None, blocking=None)
        self.assertEqual(
            result, {"error": "Confluence · rippling.atlassian.net is already connected"})


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


class RemoveSourceTests(unittest.TestCase):
    """removeSource is the real delete Disconnect never was: the row, its
    documents, its checkpoints, and its scheduled sync flow, refused while a
    worker could still be writing documents for the source."""

    class _Info:
        # The admin tier is the caller's membership in the request's project,
        # so the fake context carries one (identity.graphql._require_admin).
        context = {"user": {"id": 1, "name": "Admin", "role": "admin"},
                   "access": access_module.AccessContext(1, 7, "acme", "Acme", "admin", access_module.capabilities_for_role("admin"))}

    def _remove(self, source_row, flows=(), running=False, delete_documents=True,
                siblings=0):
        from types import SimpleNamespace
        from mari_server.identity import graphql as mutations_admin
        from mari_server.persistence.postgres import admin as admin_store

        conn = _FakeRemoveConn(source_row, flows, siblings=siblings)
        with patch.object(mutations_admin.ingest, "is_running", return_value=running), \
             patch.object(mutations_admin, "audit"), \
             patch.object(admin_store.db, "connect", return_value=conn), \
             patch.object(admin_store.access, "require_current_access",
                          return_value=SimpleNamespace(project_id=1)):
            result = mutations_admin.MutAdmin().remove_source(
                self._Info(), 42, delete_documents=delete_documents)
        return result, conn

    def test_refuses_while_a_sync_is_running(self) -> None:
        with self.assertRaisesRegex(ValueError, "still running"):
            self._remove({"id": 42, "kind": "connector", "provider": "confluence",
                          "display_name": "Confluence"}, running=True)

    def test_refuses_a_non_connector_kind(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only connector sources"):
            self._remove({"id": 42, "kind": "legacy", "provider": "notion",
                          "display_name": "Notion"})

    def test_a_missing_row_answers_false_without_deleting(self) -> None:
        result, conn = self._remove(None)
        self.assertFalse(result)
        self.assertFalse(any(sql.startswith("DELETE") for sql, _ in conn.executed))

    def test_deletes_source_checkpoints_and_flow_on_success(self) -> None:
        flows = [
            {"id": 9, "nodes": [{"kind": "trigger", "config": {}},
                                {"kind": "sync_source", "config": {"source_id": 42}}]},
            {"id": 10, "nodes": [{"kind": "sync_source", "config": {"source_id": 7}}]},
        ]
        result, conn = self._remove(
            {"id": 42, "kind": "connector", "provider": "confluence",
             "display_name": "Confluence"}, flows=flows)
        self.assertTrue(result)
        deletes = [(sql, args) for sql, args in conn.executed if sql.startswith("DELETE")]
        tables = [sql.split()[2] for sql, _ in deletes]
        # documents and the rows that hang off them without a cascade
        for table in ("tags", "edges", "findings", "changes", "watches", "documents"):
            self.assertIn(table, tables)
        self.assertIn(("DELETE FROM ingest_checkpoints WHERE project_id = %s AND provider = %s",
                       (1, "confluence")), deletes)
        self.assertIn(("DELETE FROM sources WHERE project_id = %s AND id = %s", (1, 42)), deletes)
        # only the flow whose sync_source step names source 42 goes, runs first
        self.assertIn(("DELETE FROM workflow_runs WHERE workflow_id = %s", (9,)), deletes)
        self.assertIn(("DELETE FROM workflows WHERE id = %s", (9,)), deletes)
        self.assertNotIn(("DELETE FROM workflows WHERE id = %s", (10,)), deletes)
        # sync history stays; a "removed" event is appended to it
        event_args = next(args for sql, args in conn.executed
                          if sql.startswith("INSERT INTO sync_events"))
        self.assertEqual(event_args[2], "removed: Confluence")
        self.assertFalse(any(sql.startswith("DELETE FROM sync_events") for sql, _ in conn.executed))

    def test_reconnect_adopts_the_frozen_snapshot_when_unambiguous(self) -> None:
        from types import SimpleNamespace
        from mari_server.persistence.postgres import admin as admin_store

        class Conn(_FakeRemoveConn):
            def execute(self, sql, args=()):
                normalized = " ".join(sql.split())
                self.executed.append((normalized, args))
                result = unittest.mock.Mock()
                if normalized.startswith("SELECT count(*) AS n FROM sources"):
                    result.fetchone.return_value = {"n": self.siblings}
                else:
                    result.fetchall.return_value = [{"id": 5}, {"id": 9}]
                return result

        conn = Conn(None, siblings=0)
        with unittest.mock.patch.object(admin_store.db, "connect", return_value=conn), \
             unittest.mock.patch.object(admin_store.access, "require_current_access",
                                        return_value=SimpleNamespace(project_id=1)):
            adopted = admin_store.adopt_frozen_documents("confluence", 10)
        self.assertEqual(adopted, 2)
        sql, args = next((s, a) for s, a in conn.executed if s.startswith("UPDATE documents"))
        # identity is rewritten onto the new source id, and a row whose
        # rewritten identity a fresh sync already claimed stays frozen
        self.assertIn("regexp_replace(external_id, %s, %s)", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("source_id IS NULL", sql)
        self.assertEqual(args[1:3], ("^confluence:[0-9]+:", "confluence:10:"))

    def test_reconnect_adopts_nothing_beside_a_sibling_connection(self) -> None:
        from types import SimpleNamespace
        from mari_server.persistence.postgres import admin as admin_store

        conn = _FakeRemoveConn(None, siblings=1)
        with unittest.mock.patch.object(admin_store.db, "connect", return_value=conn), \
             unittest.mock.patch.object(admin_store.access, "require_current_access",
                                        return_value=SimpleNamespace(project_id=1)):
            adopted = admin_store.adopt_frozen_documents("confluence", 10)
        # two sites of one provider: no way to know whose snapshot this was
        self.assertEqual(adopted, 0)
        self.assertFalse(any(sql.startswith("UPDATE documents") for sql, _ in conn.executed))

    def test_keeps_a_sibling_connections_checkpoints(self) -> None:
        result, conn = self._remove(
            {"id": 42, "kind": "connector", "provider": "confluence",
             "display_name": "Confluence — ENG"}, siblings=1)
        self.assertTrue(result)
        deletes = [(sql, args) for sql, args in conn.executed if sql.startswith("DELETE")]
        # checkpoint rows are keyed (provider, item), and the sibling shares
        # the provider: only this connection's rows go
        self.assertIn(("DELETE FROM ingest_checkpoints WHERE project_id = %s AND provider = %s AND item = %s",
                       (1, "confluence", "Confluence — ENG")), deletes)
        self.assertNotIn(("DELETE FROM ingest_checkpoints WHERE project_id = %s AND provider = %s",
                          (1, "confluence")), deletes)

    def test_can_keep_documents_as_a_disconnected_snapshot(self) -> None:
        result, conn = self._remove(
            {"id": 42, "kind": "connector", "provider": "confluence",
             "display_name": "Confluence"}, delete_documents=False)
        self.assertTrue(result)
        self.assertIn(("UPDATE documents SET source_id = NULL WHERE project_id = %s AND source_id = %s",
                       (1, 42)), conn.executed)
        self.assertFalse(any(sql.startswith("DELETE FROM documents") for sql, _ in conn.executed))
        event_args = next(args for sql, args in conn.executed
                          if sql.startswith("INSERT INTO sync_events"))
        self.assertEqual(event_args[3], "Removed by admin, documents retained")


class _FakeRemoveConn:
    """Just enough connection for remove_source: the FOR UPDATE probe answers
    with the configured source row, the workflows scan answers with the
    configured flows, every statement is logged."""

    def __init__(self, source_row, flows=(), siblings=0):
        self.source_row = source_row
        self.flows = list(flows)
        self.siblings = siblings
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
        result = unittest.mock.Mock()
        if normalized.startswith("SELECT id, kind, provider, display_name FROM sources"):
            result.fetchone.return_value = self.source_row
        elif normalized.startswith("SELECT count(*) AS n FROM sources"):
            result.fetchone.return_value = {"n": self.siblings}
        elif normalized.startswith("SELECT id, nodes FROM workflows"):
            result.fetchall.return_value = self.flows
        else:
            result.fetchone.return_value = None
            result.fetchall.return_value = []
        return result


if __name__ == "__main__":
    unittest.main()

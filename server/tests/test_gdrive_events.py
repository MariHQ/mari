from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from mari_server.identity import access
from mari_server.sources import gdrive_events
from mari_components import KnowledgeDocument, PollPage, PollRequest
from mari_components.connectors import GoogleDriveConfig, GoogleDriveWatch, connector_definition
from mari_components.connectors import catalog
from mari_components.errors import AuthenticationFailure, TransientFailure
from mari_components.types import Tombstone


class MemoryInbox:
    def __init__(self):
        self.keys = set()
        self.calls = []
    def enqueue(self, provider, project_id, delivery_id, payload, **kwargs):
        key = (provider, project_id, delivery_id)
        inserted = key not in self.keys
        self.keys.add(key)
        self.calls.append((provider, project_id, delivery_id, payload, kwargs))
        return len(self.keys), inserted


class Request:
    def __init__(self, token="watch-token", number="7", state="change",
                 resource_id="resource-1"):
        self.headers = {
            "X-Goog-Channel-ID": "channel-1",
            "X-Goog-Channel-Token": token,
            "X-Goog-Resource-ID": resource_id,
            "X-Goog-Resource-State": state,
            "X-Goog-Message-Number": number,
        }


class DriveWebhookTests(unittest.TestCase):
    @staticmethod
    def channel():
        return {"id": 3, "project_id": 9, "source_id": 5, "channel_id": "channel-1",
                "token_hash": hashlib.sha256(b"watch-token").hexdigest(),
                "resource_id": "resource-1", "source_status": "active",
                "project_status": "active"}

    def test_authenticated_notification_is_durable_before_204_and_replay_dedupes(self):
        inbox = MemoryInbox()
        with patch.object(gdrive_events.event_store, "drive_channel", return_value=self.channel()), \
             patch.object(gdrive_events.event_store, "observe_drive_message") as observe, \
             patch.object(gdrive_events, "DEFAULT_INBOX", inbox):
            first = asyncio.run(gdrive_events.gdrive_webhook(Request()))
            replay = asyncio.run(gdrive_events.gdrive_webhook(Request()))
        self.assertEqual(first.status_code, 204)
        self.assertEqual(replay.status_code, 204)
        self.assertEqual(replay.headers["x-mari-duplicate"], "true")
        self.assertEqual(inbox.calls[0][0:3], ("gdrive", 9, "channel-1:7"))
        self.assertEqual(inbox.calls[0][4]["coalesce_key"], "source:5")
        self.assertEqual(observe.call_count, 2)

    def test_bad_token_resource_or_message_number_is_rejected(self):
        with patch.object(gdrive_events.event_store, "drive_channel", return_value=self.channel()):
            self.assertEqual(asyncio.run(gdrive_events.gdrive_webhook(Request(token="wrong"))).status_code, 401)
            self.assertEqual(asyncio.run(gdrive_events.gdrive_webhook(Request(resource_id="wrong"))).status_code, 401)
            self.assertEqual(asyncio.run(gdrive_events.gdrive_webhook(Request(number="nope"))).status_code, 400)

    def test_storage_failure_returns_retryable_503(self):
        class Broken:
            def enqueue(self, *_args, **_kwargs):
                raise OSError("postgres unavailable")
        with patch.object(gdrive_events.event_store, "drive_channel", return_value=self.channel()), \
             patch.object(gdrive_events, "DEFAULT_INBOX", Broken()):
            response = asyncio.run(gdrive_events.gdrive_webhook(Request()))
        self.assertEqual(response.status_code, 503)


class DriveWatchSetupTests(unittest.TestCase):
    def setUp(self):
        self.context = access.AccessContext(1, 9, "acme", "Acme", "admin", access.CAPABILITIES)
        self.source = {"id": 5, "project_id": 9, "config": {"cursor": "changes:start"}}

    def test_watch_persists_route_before_call_and_uses_https_callback(self):
        calls = []
        watched = GoogleDriveWatch("channel", "resource-1", 1_800_000_000_000)
        with patch.object(gdrive_events, "_source", return_value=self.source), \
             patch.object(gdrive_events.config, "get", return_value="https://mari.example.test"), \
             patch.object(gdrive_events.event_store, "create_drive_watch",
                          side_effect=lambda *_args: calls.append("persisted")), \
             patch.object(gdrive_events.event_store, "activate_drive_watch") as activate, \
             patch.object(gdrive_events, "start_google_drive_watch", return_value=watched) as watch:
            result = gdrive_events.create_watch(gdrive_events.DriveWatchIn(source_id=5), self.context)
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["persisted"])
        activate.assert_called_once()
        self.assertEqual(watch.call_args.args[1:3],
                         ("start", "https://mari.example.test/webhooks/google-drive"))
        self.assertNotIn(watch.call_args.args[4], json.dumps(result))

    def test_watch_fails_explicitly_without_cursor_or_https(self):
        with patch.object(gdrive_events, "_source",
                return_value={**self.source, "config": {"cursor": ""}}), \
             self.assertRaisesRegex(HTTPException, "initial poll"):
            gdrive_events.create_watch(gdrive_events.DriveWatchIn(source_id=5), self.context)
        with patch.object(gdrive_events, "_source", return_value=self.source), \
             patch.object(gdrive_events.config, "get", return_value="http://localhost:8000"), \
             self.assertRaisesRegex(HTTPException, "HTTPS"):
            gdrive_events.create_watch(gdrive_events.DriveWatchIn(source_id=5), self.context)

    def test_due_watch_is_replaced_under_project_scope(self):
        rows = [{"source_id": 5, "project_id": 9, "slug": "acme", "name": "Acme"}]
        seen = []
        def renew(body, current):
            seen.append((body.source_id, current.project_id))
            return {"ok": True}
        with patch.object(gdrive_events.event_store, "due_drive_watches", return_value=rows), \
             patch.object(gdrive_events, "create_watch", side_effect=renew):
            self.assertEqual(gdrive_events.renew_due_watches(), 1)
        self.assertEqual(seen, [(5, 9)])


class DriveChangesTests(unittest.TestCase):
    CHANNEL = {"channel_id": "channel-1", "source_id": 5, "project_id": 9,
               "config": {"cursor": "changes:start", "access_token": "ya29.secret"},
               "provider": "gdrive", "display_name": "Drive", "source_status": "active",
               "project_status": "active", "project_slug": "acme", "project_name": "Acme"}

    def test_worker_drains_all_pages_and_persists_each_checkpoint(self):
        pages = [PollPage(next_cursor="changes:start", next_checkpoint="changes:middle",
                          snapshot_complete=False),
                 PollPage(next_cursor="changes:end", snapshot_complete=True)]
        definition = Mock()
        definition.poll.side_effect = lambda _cfg, _request, **_kwargs: iter([pages.pop(0)])
        with patch.object(gdrive_events.event_store, "drive_channel", return_value=dict(self.CHANNEL)), \
             patch.object(gdrive_events.ingest, "is_running", return_value=False), \
             patch.object(gdrive_events, "connector_definition", return_value=definition), \
             patch.object(gdrive_events, "_apply_poll") as apply_poll, \
             patch.object(gdrive_events.event_store, "update_drive_cursor") as update_cursor:
            gdrive_events.process_gdrive_delivery({"project_id": 9,
                "payload": {"channel_id": "channel-1"}})
        self.assertEqual([call.args[1].cursor for call in definition.poll.call_args_list],
                         ["changes:start", "changes:middle"])
        self.assertEqual(apply_poll.call_count, 2)
        # the cursor alone travels to the store, never the whole config copy
        self.assertEqual([call.args for call in update_cursor.call_args_list],
                         [(5, "changes:middle", "middle"), (5, "changes:end", "end")])

    def test_worker_yields_to_a_running_sweep_and_retries_later(self):
        definition = Mock()
        with patch.object(gdrive_events.event_store, "drive_channel", return_value=dict(self.CHANNEL)), \
             patch.object(gdrive_events.ingest, "is_running", return_value=True), \
             patch.object(gdrive_events, "connector_definition", return_value=definition), \
             patch.object(gdrive_events.event_store, "update_drive_cursor") as update_cursor:
            with self.assertRaisesRegex(TransientFailure, "scheduled sync .* is running"):
                gdrive_events.process_gdrive_delivery({"project_id": 9,
                    "payload": {"channel_id": "channel-1"}})
        definition.poll.assert_not_called()
        update_cursor.assert_not_called()

    def test_page_apply_writes_only_its_own_hash_entries(self):
        # The poll worker owns the manifest; this worker owns the entries for
        # the files this page touched, merged in the documents transaction.
        source = {"id": 5, "project_id": 9}
        config = {"access_token": "ya29.secret", "item_hashes": {"kept": "k1", "gone": "g1"}}
        poll = PollPage(
            upserts=(KnowledgeDocument("doc-1", "Doc", "Body", revision="r7"),),
            tombstones=(Tombstone("gone"),), next_cursor="changes:end", snapshot_complete=True)
        conn = Mock()
        with patch.object(gdrive_events.document_index, "connection", return_value=_Context(conn)), \
             patch.object(gdrive_events.document_index, "chunk_settings", return_value=(100, 10)), \
             patch.object(gdrive_events.document_index, "upsert_document", return_value=(91, True)), \
             patch.object(gdrive_events.document_index, "sync_chunks"), \
             patch.object(gdrive_events.document_repository, "source_document_paths",
                          return_value=[{"id": 92, "source_path": "gdrive/gone"}]), \
             patch.object(gdrive_events.document_index, "delete_documents") as delete, \
             patch.object(gdrive_events.connector_sync, "merge_config") as merge, \
             patch.object(gdrive_events, "invalidate_search"):
            gdrive_events._apply_poll(source, config, poll)
        delete.assert_called_once_with(conn, [92])
        merge.assert_called_once_with(conn, 5, {}, hashes={"doc-1": "r7"}, dropped=["gone"])
        conn.commit.assert_called_once()
        # the in-memory copy still tracks what was seen for this drain
        self.assertEqual(config["item_hashes"], {"kept": "k1", "doc-1": "r7"})

    def test_410_runs_full_poll_reconciliation_and_replaces_cursor(self):
        source = {"id": 5, "source_id": 5, "project_id": 9, "channel_id": "channel-1"}
        refreshed = {"id": 5, "project_id": 9, "config": {"cursor": "changes:fresh"}}
        with patch.object(gdrive_events.event_store, "mark_drive_resync") as mark, \
             patch.object(gdrive_events.event_store, "restore_drive_cursor") as restore, \
             patch.object(gdrive_events.ingest, "run_sync", return_value={}), \
             patch.object(gdrive_events, "_source", return_value=refreshed):
            gdrive_events._full_reconcile(source, {}, "channel-1")
        mark.assert_called_once_with("channel-1")
        restore.assert_called_once_with(5, "fresh")

    def test_worker_persists_a_refreshed_token_through_merge_config(self):
        # The poll refreshes on 401; the new token has to reach the stored
        # config, or the next delivery and the watch renewal refresh again.
        def poll(cfg, _request, *, http, on_refresh):
            on_refresh({"access_token": "fresh"})
            self.assertEqual(cfg["access_token"], "fresh")
            return iter([PollPage(next_cursor="changes:end", snapshot_complete=True)])
        definition = Mock()
        definition.poll.side_effect = poll
        conn = Mock()
        with patch.object(gdrive_events.event_store, "drive_channel", return_value=dict(self.CHANNEL)), \
             patch.object(gdrive_events.ingest, "is_running", return_value=False), \
             patch.object(gdrive_events, "connector_definition", return_value=definition), \
             patch.object(gdrive_events, "_apply_poll"), \
             patch.object(gdrive_events.event_store, "update_drive_cursor"), \
             patch.object(gdrive_events.document_index, "connection", return_value=_Context(conn)), \
             patch.object(gdrive_events.connector_sync, "merge_config") as merge:
            gdrive_events.process_gdrive_delivery({"project_id": 9,
                "payload": {"channel_id": "channel-1"}})
        merge.assert_called_once_with(conn, 5, {"access_token": "fresh"}, synced=False)
        conn.commit.assert_called_once()


class DriveTokenRefreshTests(unittest.TestCase):
    REFRESHABLE = {"cursor": "changes:start", "access_token": "stale",
                   "refresh_token": "1//refresh", "client_id": "client", "client_secret": "secret"}

    def setUp(self):
        self.context = access.AccessContext(1, 9, "acme", "Acme", "admin", access.CAPABILITIES)

    def test_watch_refreshes_a_rejected_token_persists_it_and_retries_once(self):
        source = {"id": 5, "project_id": 9, "config": dict(self.REFRESHABLE)}
        attempts = []
        def watch(config, *_args, **_kwargs):
            attempts.append(config.access_token)
            if len(attempts) == 1:
                raise AuthenticationFailure("provider rejected credentials (HTTP 401)")
            return GoogleDriveWatch("channel", "resource-1", None)
        conn = Mock()
        with patch.object(gdrive_events, "_source", return_value=source), \
             patch.object(gdrive_events.config, "get", return_value="https://mari.example.test"), \
             patch.object(gdrive_events.event_store, "create_drive_watch"), \
             patch.object(gdrive_events.event_store, "activate_drive_watch") as activate, \
             patch.object(gdrive_events.event_store, "update_drive_watch") as failed, \
             patch.object(gdrive_events, "start_google_drive_watch", side_effect=watch), \
             patch.object(catalog, "refresh_google_access_token", return_value="fresh"), \
             patch.object(gdrive_events.document_index, "connection", return_value=_Context(conn)), \
             patch.object(gdrive_events.connector_sync, "merge_config") as merge:
            result = gdrive_events.create_watch(gdrive_events.DriveWatchIn(source_id=5), self.context)
        self.assertTrue(result["ok"])
        self.assertEqual(attempts, ["stale", "fresh"])
        activate.assert_called_once()
        failed.assert_not_called()
        # only the minted keys are written, through the shared merge writer
        merge.assert_called_once()
        self.assertEqual(merge.call_args.args[:2], (conn, 5))
        updates = merge.call_args.args[2]
        self.assertEqual(updates["access_token"], "fresh")
        self.assertIn("access_token_refreshed_at", updates)
        self.assertEqual(set(updates), {"access_token", "access_token_refreshed_at"})
        conn.commit.assert_called_once()

    def test_watch_without_a_refresh_grant_still_fails_as_502(self):
        source = {"id": 5, "project_id": 9, "config": {"cursor": "changes:start", "access_token": "stale"}}
        with patch.object(gdrive_events, "_source", return_value=source), \
             patch.object(gdrive_events.config, "get", return_value="https://mari.example.test"), \
             patch.object(gdrive_events.event_store, "create_drive_watch"), \
             patch.object(gdrive_events.event_store, "update_drive_watch") as failed, \
             patch.object(gdrive_events, "start_google_drive_watch",
                          side_effect=AuthenticationFailure("provider rejected credentials (HTTP 401)")), \
             patch.object(gdrive_events.connector_sync, "merge_config") as merge, \
             self.assertRaisesRegex(HTTPException, "rejected credentials"):
            gdrive_events.create_watch(gdrive_events.DriveWatchIn(source_id=5), self.context)
        failed.assert_called_once()
        merge.assert_not_called()

    def test_catalog_poll_reports_minted_keys_before_retrying_with_them(self):
        polled = []
        def poll_operation(config, _request, *, http):
            polled.append(config.access_token)
            if config.access_token == "stale":
                raise AuthenticationFailure("provider rejected credentials (HTTP 401)")
            yield PollPage(snapshot_complete=True)
        definition = catalog.ConnectorDefinition(
            "fake", "Fake", "", (), "",
            lambda values: GoogleDriveConfig(str(values.get("access_token") or ""), ""),
            lambda *_args, **_kwargs: None, poll_operation,
            refresh_operation=lambda _values, _http: {"access_token": "fresh"},
        )
        values = dict(self.REFRESHABLE)
        persisted = []
        pages = list(definition.poll(values, PollRequest(), http=lambda _request: None,
                                     on_refresh=persisted.append))
        self.assertEqual(len(pages), 1)
        self.assertEqual(polled, ["stale", "fresh"])
        self.assertEqual(persisted, [{"access_token": "fresh"}])
        # the caller's mapping is not mutated behind its back
        self.assertEqual(values["access_token"], "stale")

    def test_drive_refresh_returns_the_token_and_when_it_was_minted(self):
        with patch.object(catalog, "refresh_google_access_token", return_value="fresh"):
            refreshed = connector_definition("gdrive").refresh(self.REFRESHABLE, http=lambda _request: None)
        self.assertEqual(refreshed["access_token"], "fresh")
        minted = dt.datetime.fromisoformat(refreshed["access_token_refreshed_at"])
        self.assertIsNotNone(minted.tzinfo)
        self.assertLess(dt.datetime.now(dt.timezone.utc) - minted, dt.timedelta(minutes=1))
        with self.assertRaisesRegex(AuthenticationFailure, "no refresh grant"):
            connector_definition("gdrive").refresh({"access_token": "stale"}, http=lambda _request: None)


class _Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


if __name__ == "__main__":
    unittest.main()

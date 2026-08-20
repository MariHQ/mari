from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from mari_server.identity import access
from mari_server.sources import gdrive_events
from mari_components import PollPage
from mari_components.connectors import GoogleDriveWatch


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
        with patch.object(gdrive_events, "q1", return_value=self.channel()), \
             patch.object(gdrive_events, "exec_") as execute, \
             patch.object(gdrive_events, "DEFAULT_INBOX", inbox):
            first = asyncio.run(gdrive_events.gdrive_webhook(Request()))
            replay = asyncio.run(gdrive_events.gdrive_webhook(Request()))
        self.assertEqual(first.status_code, 204)
        self.assertEqual(replay.status_code, 204)
        self.assertEqual(replay.headers["x-mari-duplicate"], "true")
        self.assertEqual(inbox.calls[0][0:3], ("gdrive", 9, "channel-1:7"))
        self.assertEqual(inbox.calls[0][4]["coalesce_key"], "source:5")
        self.assertEqual(execute.call_count, 2)

    def test_bad_token_resource_or_message_number_is_rejected(self):
        with patch.object(gdrive_events, "q1", return_value=self.channel()):
            self.assertEqual(asyncio.run(gdrive_events.gdrive_webhook(Request(token="wrong"))).status_code, 401)
            self.assertEqual(asyncio.run(gdrive_events.gdrive_webhook(Request(resource_id="wrong"))).status_code, 401)
            self.assertEqual(asyncio.run(gdrive_events.gdrive_webhook(Request(number="nope"))).status_code, 400)

    def test_storage_failure_returns_retryable_503(self):
        class Broken:
            def enqueue(self, *_args, **_kwargs):
                raise OSError("postgres unavailable")
        with patch.object(gdrive_events, "q1", return_value=self.channel()), \
             patch.object(gdrive_events, "DEFAULT_INBOX", Broken()):
            response = asyncio.run(gdrive_events.gdrive_webhook(Request()))
        self.assertEqual(response.status_code, 503)


class DriveWatchSetupTests(unittest.TestCase):
    def setUp(self):
        self.context = access.AccessContext(1, 9, "acme", "Acme", "admin", access.CAPABILITIES)
        self.source = {"id": 5, "project_id": 9, "config": {"cursor": "changes:start"}}

    def test_watch_persists_route_before_call_and_uses_https_callback(self):
        calls = []
        def execute(sql, args=()):
            calls.append((" ".join(sql.split()), args))
        watched = GoogleDriveWatch("channel", "resource-1", 1_800_000_000_000)
        with patch.object(gdrive_events, "_source", return_value=self.source), \
             patch.object(gdrive_events.config, "get", return_value="https://mari.example.test"), \
             patch.object(gdrive_events, "exec_", side_effect=execute), \
             patch.object(gdrive_events, "start_google_drive_watch", return_value=watched) as watch:
            result = gdrive_events.create_watch(gdrive_events.DriveWatchIn(source_id=5), self.context)
        self.assertTrue(result["ok"])
        self.assertTrue(calls[0][0].startswith("INSERT INTO gdrive_watch_channels"))
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
        with patch.object(gdrive_events, "q", return_value=rows), \
             patch.object(gdrive_events, "exec_"), \
             patch.object(gdrive_events, "create_watch", side_effect=renew):
            self.assertEqual(gdrive_events.renew_due_watches(), 1)
        self.assertEqual(seen, [(5, 9)])


class DriveChangesTests(unittest.TestCase):
    def test_worker_drains_all_pages_and_persists_each_checkpoint(self):
        channel = {"channel_id": "channel-1", "source_id": 5, "project_id": 9,
                   "config": {"cursor": "changes:start"}, "provider": "gdrive",
                   "display_name": "Drive", "source_status": "active", "project_status": "active",
                   "project_slug": "acme", "project_name": "Acme"}
        pages = [PollPage(next_cursor="changes:start", next_checkpoint="changes:middle",
                          snapshot_complete=False),
                 PollPage(next_cursor="changes:end", snapshot_complete=True)]
        definition = Mock()
        definition.poll.side_effect = lambda _cfg, _request, **_kwargs: iter([pages.pop(0)])
        with patch.object(gdrive_events, "q1", return_value=channel), \
             patch.object(gdrive_events, "connector_definition", return_value=definition), \
             patch.object(gdrive_events, "_apply_poll") as apply_poll, \
             patch.object(gdrive_events, "exec_"):
            gdrive_events.process_gdrive_delivery({"project_id": 9,
                "payload": {"channel_id": "channel-1"}})
        self.assertEqual([call.args[1].cursor for call in definition.poll.call_args_list],
                         ["changes:start", "changes:middle"])
        self.assertEqual(apply_poll.call_count, 2)

    def test_410_runs_full_poll_reconciliation_and_replaces_cursor(self):
        source = {"id": 5, "source_id": 5, "project_id": 9, "channel_id": "channel-1"}
        refreshed = {"id": 5, "project_id": 9, "config": {"cursor": "changes:fresh"}}
        with patch.object(gdrive_events, "exec_") as execute, \
             patch.object(gdrive_events.ingest, "run_sync", return_value={}), \
             patch.object(gdrive_events, "_source", return_value=refreshed):
            gdrive_events._full_reconcile(source, {}, "channel-1")
        self.assertIn("needs_full_resync", execute.call_args_list[0].args[0])
        self.assertEqual(execute.call_args_list[-1].args[1], ("fresh", 5))


if __name__ == "__main__":
    unittest.main()

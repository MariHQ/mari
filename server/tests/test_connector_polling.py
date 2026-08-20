from __future__ import annotations

import json
import unittest
import urllib.parse
from unittest.mock import patch

import connect_sync
import connectors
import connectors_api
from connectors import confluence, gdrive, slack


class ConnectorContractTests(unittest.TestCase):
    def test_catalog_hides_upload_and_website(self) -> None:
        registry = type("Registry", (), {
            "refresh": lambda self: None,
            "values": lambda self: [{
                "provider": {"key": "website", "name": "Website", "fields": []},
                "error": None,
            }],
        })()
        with patch.object(connectors_api.connectors, "REGISTRY", registry), \
             patch.object(connectors_api, "_connected_map", return_value={}):
            keys = [item["key"] for item in connectors_api.catalog()]
        self.assertIn("github", keys)
        self.assertNotIn("upload", keys)
        self.assertNotIn("website", keys)

    def test_requested_poll_connectors_are_discoverable(self) -> None:
        connectors.REGISTRY.refresh()
        for key in ("confluence", "slack", "gdrive"):
            entry = connectors.REGISTRY[key]
            self.assertIsNone(entry.get("error"))
            self.assertTrue(callable(entry["validate"]))
            self.assertTrue(callable(entry["list_items"]))

    def test_connector_config_masks_credentials_and_drops_hash_checkpoint_map(self) -> None:
        cfg = {"provider_key": "gdrive", "access_token": "ya29.secret", "folder_id": "abc",
               "item_hashes": {"doc": "hash"}, "cursor": "2026-08-19"}
        safe = connect_sync.masked_config("gdrive:abc", cfg)
        self.assertEqual(safe["access_token"], connect_sync.MASK)
        self.assertEqual(safe["folder_id"], "abc")
        self.assertNotIn("item_hashes", safe)


class ConfluencePollingTests(unittest.TestCase):
    CFG = {"site_url": "acme.atlassian.net", "email": "a@acme.test", "api_token": "secret"}

    def test_validate_reports_auth_failure_without_leaking_credentials(self) -> None:
        with patch.object(confluence, "_http", return_value=(403, b"forbidden")):
            error = confluence.validate(self.CFG)
        self.assertIn("403 Forbidden", error or "")
        self.assertNotIn("secret", error or "")

    def test_poll_converts_storage_body_and_advances_cursor(self) -> None:
        page = {"id": "42", "title": "Runbook", "body": {"storage": {"value":
                "<h2>Deploy</h2><ul><li>Drain traffic</li></ul><ac:structured-macro ac:name='code'><ac:plain-text-body><![CDATA[kubectl apply]]></ac:plain-text-body></ac:structured-macro>"}},
                "version": {"number": 7}, "history": {"lastUpdated": {"when": "2026-08-19T10:00:00Z"}}}
        with patch.object(confluence, "_get", return_value={"results": [page], "size": 1}):
            items, cursor = confluence.list_items(self.CFG, "2026-08-18T00:00:00Z")
        self.assertEqual(cursor, "2026-08-19T10:00:00Z")
        self.assertEqual(items[0]["path"], "42")
        self.assertIn("## Deploy", items[0]["body"])
        self.assertIn("kubectl apply", items[0]["body"])
        self.assertEqual(items[0]["hash_hint"], "7")

    def test_incremental_poll_does_not_reemit_old_pages(self) -> None:
        old = {"id": "1", "title": "Old", "body": {"storage": {"value": "<p>old</p>"}},
               "version": {"number": 1}, "history": {"lastUpdated": {"when": "2026-08-01T00:00:00Z"}}}
        with patch.object(confluence, "_get", return_value={"results": [old], "size": 1}):
            items, cursor = confluence.list_items(self.CFG, "2026-08-10T00:00:00Z")
        self.assertEqual(items, [])
        self.assertEqual(cursor, "2026-08-10T00:00:00Z")


class GoogleDrivePollingTests(unittest.TestCase):
    CFG = {"access_token": "token", "folder_id": "folder'id"}

    def test_poll_paginates_exports_docs_downloads_text_and_advances_cursor(self) -> None:
        calls: list[str] = []

        def http(method, url, headers=None, body=None, timeout=30):
            calls.append(url)
            if "/files?" in url:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                self.assertIn("'folder\\'id' in parents", query["q"][0])
                if "pageToken" not in query:
                    return 200, json.dumps({"nextPageToken": "next", "files": [{
                        "id": "doc/1", "name": "Plan", "mimeType": gdrive._DOC_MIME,
                        "modifiedTime": "2026-08-19T09:00:00Z"}]}).encode()
                return 200, json.dumps({"files": [{"id": "txt", "name": "Notes.md",
                    "mimeType": "text/markdown", "modifiedTime": "2026-08-19T11:00:00Z",
                    "md5Checksum": "abc"}]}).encode()
            return 200, b"document body"

        with patch.object(gdrive, "_http", side_effect=http):
            items, cursor = gdrive.list_items(self.CFG, "2026-08-18T00:00:00Z")
        self.assertEqual([x["title"] for x in items], ["Plan", "Notes.md"])
        self.assertEqual(cursor, "2026-08-19T11:00:00Z")
        self.assertTrue(any("/export?mimeType=text%2Fplain" in u for u in calls))
        self.assertTrue(any("alt=media" in u for u in calls))


class SlackPollingTests(unittest.TestCase):
    def test_event_refresh_refetches_complete_thread_as_one_acl_aggregate(self) -> None:
        response = {"ok": True, "messages": [
            {"type": "message", "ts": "100.0", "user": "U1", "text": "Root question"},
            {"type": "message", "ts": "101.0", "thread_ts": "100.0",
             "user": "U2", "text": "Follow-up answer"},
        ]}
        with patch.object(slack, "_call", return_value=response), \
             patch.object(slack, "_user_map", return_value={"U1": "Ana", "U2": "Ben"}):
            item = slack.thread_item("xoxb", "C1", "100.0")
        self.assertEqual(item["path"], "thread/C1/100.0")
        self.assertIn("@Ana: Root question", item["body"])
        self.assertIn("@Ben: Follow-up answer", item["body"])
        self.assertEqual(item["acl"].principals, ("channel:C1",))
        self.assertEqual(item["hash_hint"], "101.000000")

    def test_incremental_poll_rebuilds_cursor_day_and_skips_bot_messages(self) -> None:
        old = 1_723_680_000.0
        new = old + 120
        channels = [{"id": "C1", "name": "engineering", "is_member": True},
                    {"id": "C2", "name": "random", "is_member": True}]
        messages = [
            {"type": "message", "ts": f"{old:.6f}", "user": "U1", "text": "old context"},
            {"type": "message", "ts": f"{new:.6f}", "user": "U1", "text": "hello <@U2> <https://x.test|link>"},
            {"type": "message", "ts": f"{new + 1:.6f}", "bot_id": "B1", "text": "ignore"},
        ]
        with patch.object(slack, "_user_map", return_value={"U1": "ana", "U2": "ben"}), \
             patch.object(slack, "_channels", return_value=channels), \
             patch.object(slack, "_channel_messages", return_value=messages) as history:
            items, cursor = slack.list_items({"bot_token": "x", "channels": "engineering"}, f"{old:.6f}")
        self.assertEqual(len(items), 1)
        self.assertIn("@ana: old context", items[0]["body"])
        self.assertIn("hello @ben link", items[0]["body"])
        self.assertNotIn("ignore", items[0]["body"])
        self.assertEqual(cursor, f"{new:.6f}")
        self.assertEqual(history.call_count, 1)
        self.assertIsNotNone(history.call_args.args[2])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from connectors import confluence, gdrive, jira, linear, slack, website, zendesk


class RemainingConnectorConformanceTests(unittest.TestCase):
    def test_jira_cap_holds_cursor_and_emits_acl(self):
        issue = {"id": "1", "key": "ENG-1", "fields": {"summary": "A", "updated": "2026-08-19T10:00:00Z"}}
        with patch.object(jira, "MAX_PAGES", 1), patch.object(
                jira, "_get", return_value={"issues": [issue], "total": 2}):
            result = jira.list_items({}, "2026-08-18T00:00:00Z")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-18T00:00:00Z")
        self.assertEqual(result.items[0]["acl"].visibility, "connector_scope")

    def test_linear_cap_holds_cursor_and_emits_acl(self):
        node = {"identifier": "ENG-1", "title": "A", "updatedAt": "2026-08-19T10:00:00Z"}
        page = {"issues": {"nodes": [node], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}
        with patch.object(linear, "MAX_PAGES", 1), patch.object(linear, "_graphql", return_value=page):
            result = linear.list_items({}, "2026-08-18T00:00:00Z")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-18T00:00:00Z")
        self.assertEqual(result.items[0]["acl"].visibility, "connector_scope")

    def test_zendesk_cap_holds_cursor_and_emits_acl(self):
        article = {"id": 1, "title": "A", "body": "<p>body</p>", "updated_at": "2026-08-19T10:00:00Z"}
        with patch.object(zendesk, "MAX_PAGES", 1), patch.object(
                zendesk, "_get", return_value={"articles": [article], "next_page": "https://next"}):
            result = zendesk.list_items({"subdomain": "acme"}, "2026-08-18T00:00:00Z")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-18T00:00:00Z")
        self.assertEqual(result.items[0]["acl"].visibility, "connector_scope")

    def test_website_frontier_cap_holds_cursor_and_is_public(self):
        response = (200, b"<html><title>A</title><body>body</body></html>",
                    {"Content-Type": "text/html"}, "https://example.com/a")
        with patch.object(website, "MAX_PAGES", 1), \
             patch.object(website, "_sitemap_urls", return_value=(["https://example.com/a", "https://example.com/b"], True)), \
             patch.object(website, "_fetch", return_value=response):
            result = website.list_items({"start_url": "https://example.com"}, "2026-08-18T00:00:00+00:00")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-18T00:00:00+00:00")
        self.assertEqual(result.items[0]["acl"].visibility, "public")

    def test_confluence_checkpoint_resumes_without_advancing_timestamp_cursor(self):
        page = {"id": "42", "title": "Runbook", "body": {"storage": {"value": "body"}},
                "version": {"number": 1}, "history": {"lastUpdated": {"when": "2026-08-19T10:00:00Z"}}}
        with patch.object(confluence, "MAX_PAGES", 1), patch.object(
                confluence, "_get", return_value={"results": [page], "size": confluence.PAGE_SIZE}):
            result = confluence.list_items({}, "2026-08-18T00:00:00Z")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-18T00:00:00Z")
        self.assertTrue(result.checkpoint.startswith(f"page:{confluence.PAGE_SIZE}|"))

    def test_drive_changes_emit_tombstones_and_advance_native_token(self):
        payload = {"changes": [{"fileId": "gone", "removed": True}], "newStartPageToken": "next"}
        with patch.object(gdrive, "_request", return_value=(200, json.dumps(payload).encode())):
            result = gdrive.list_items({"access_token": "x"}, "changes:old")
        self.assertEqual(result.tombstones, ["gone"])
        self.assertEqual(result.cursor, "changes:next")
        self.assertTrue(result.snapshot_complete)

    def test_drive_refreshes_once_after_expired_access_token(self):
        config = {"access_token": "expired", "refresh_token": "refresh", "client_id": "id", "client_secret": "secret"}
        with patch.object(gdrive, "_http", side_effect=[
                (401, b"{}"), (200, b'{"access_token":"fresh"}'), (200, b'{"user":{}}')]) as http:
            status, _ = gdrive._request(config, "GET", "https://www.googleapis.com/drive/v3/about")
        self.assertEqual(status, 200)
        self.assertEqual(config["access_token"], "fresh")
        self.assertEqual(http.call_count, 3)

    def test_slack_threads_and_deletes_rebuild_day_documents(self):
        root = {"type": "message", "ts": "1724025600.0", "user": "U1", "text": "root", "reply_count": 1,
                "latest_reply": "1724025660.0"}
        reply = {"type": "message", "ts": "1724025660.0", "thread_ts": "1724025600.0", "user": "U2", "text": "reply"}
        with patch.object(slack, "_user_map", return_value={"U1": "one", "U2": "two"}), \
             patch.object(slack, "_channels", return_value=slack._PagedList([
                 {"id": "C1", "name": "general", "is_member": True}], complete=True)), \
             patch.object(slack, "_channel_messages", return_value=slack._PagedList([root], complete=True)), \
             patch.object(slack, "_thread_replies", return_value=slack._PagedList([reply], complete=True)):
            result = slack.list_items({"bot_token": "x"}, None)
        self.assertIn("↳", result.items[0]["body"])
        self.assertEqual(result.items[0]["acl"].principals, ("channel:C1",))
        self.assertEqual(result.cursor, "1724025660.000000")


if __name__ == "__main__":
    unittest.main()

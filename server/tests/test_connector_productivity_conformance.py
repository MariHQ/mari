from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from connectors import airtable, asana, notion, trello
from connectors._protocol import ACLMetadata, PollResult, call_with_retry


def _retry_without_sleep(fn):
    return call_with_retry(fn, sleep=lambda _seconds: None)


class AirtableConformanceTests(unittest.TestCase):
    CFG = {"pat": "pat-test", "base_id": "app-test"}

    def test_poll_is_typed_acl_scoped_and_marks_record_cap_incomplete(self) -> None:
        table = {"id": "tbl1", "name": "Roadmap", "fields": [{"name": "Name"}]}
        record = {"id": "rec1", "createdTime": "2026-08-19T10:00:00Z",
                  "fields": {"Name": "Launch"}}
        with patch.object(airtable, "_MAX_RECORDS", 1), \
             patch.object(airtable, "_get_json", side_effect=[
                 {"tables": [table]}, {"records": [record], "offset": "more"},
             ]):
            result = airtable.list_items(self.CFG, "ignored")
        self.assertIsInstance(result, PollResult)
        self.assertFalse(result.snapshot_complete)
        self.assertIsNone(result.cursor)  # Airtable exposes no change cursor here.
        self.assertEqual(result.tombstones, [])
        self.assertEqual(result.items[0]["acl"], ACLMetadata("connector_scope"))

    def test_http_boundary_uses_shared_retry(self) -> None:
        with patch.object(airtable, "call_with_retry", side_effect=_retry_without_sleep), \
             patch.object(airtable, "_http", side_effect=[
                 ConnectionError("network unavailable"),
                 (200, b'{"tables": []}'),
             ]) as http:
            self.assertEqual(airtable._get_json(self.CFG, "https://api.airtable.com/test"),
                             {"tables": []})
        self.assertEqual(http.call_count, 2)


class AsanaConformanceTests(unittest.TestCase):
    CFG = {"pat": "asana-test", "workspace": "ws1"}

    def test_poll_is_typed_acl_scoped_and_keeps_cursor_when_capped(self) -> None:
        project = {"gid": "p1", "name": "Migration",
                   "modified_at": "2026-08-19T10:00:00Z"}
        task = {"gid": "t1", "name": "Cut over", "completed": False,
                "modified_at": "2026-08-19T11:00:00Z"}
        projects = asana._PagedList([project], complete=True)
        probe = asana._PagedList([task], complete=False, checkpoint="next-task")
        tasks = asana._PagedList([task], complete=True)
        with patch.object(asana, "_get_paginated",
                          side_effect=[projects, probe, tasks]):
            result = asana.list_items(self.CFG, "2026-08-18T00:00:00Z")
        self.assertIsInstance(result, PollResult)
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-18T00:00:00Z")
        self.assertEqual(result.checkpoint, "next-task")
        self.assertEqual(result.tombstones, [])
        self.assertEqual(result.items[0]["acl"], ACLMetadata("connector_scope"))

    def test_pagination_boundary_uses_shared_retry_and_reports_cap(self) -> None:
        response = json.dumps({"data": [], "next_page": {"offset": "more"}}).encode()
        with patch.object(asana, "_MAX_PAGES", 1), \
             patch.object(asana, "call_with_retry", side_effect=_retry_without_sleep), \
             patch.object(asana, "_http", side_effect=[
                 ConnectionError("network unavailable"), (200, response),
             ]) as http:
            page = asana._get_paginated(self.CFG, "/projects", {})
        self.assertEqual(http.call_count, 2)
        self.assertFalse(page.complete)
        self.assertEqual(page.checkpoint, "more")


class TrelloConformanceTests(unittest.TestCase):
    CFG = {"api_key": "key", "token": "token"}

    def test_poll_is_typed_acl_scoped_and_does_not_invent_tombstones(self) -> None:
        board = {"id": "b1", "name": "Platform",
                 "dateLastActivity": "2026-08-19T10:00:00Z"}
        with patch.object(trello, "_get_json", side_effect=[
            [board], [{"id": "l1", "name": "Doing"}],
            [{"id": "c1", "idList": "l1", "name": "Ship", "desc": "Safely"}],
        ]):
            result = trello.list_items(self.CFG, "2026-08-18T00:00:00Z")
        self.assertIsInstance(result, PollResult)
        self.assertTrue(result.snapshot_complete)
        self.assertEqual(result.cursor, "2026-08-19T10:00:00Z")
        self.assertEqual(result.tombstones, [])
        self.assertEqual(result.items[0]["acl"], ACLMetadata("connector_scope"))

    def test_http_boundary_uses_shared_retry(self) -> None:
        with patch.object(trello, "call_with_retry", side_effect=_retry_without_sleep), \
             patch.object(trello, "_http", side_effect=[
                 ConnectionError("network unavailable"), (200, b"[]"),
             ]) as http:
            self.assertEqual(trello._get_json(self.CFG, "/members/me/boards"), [])
        self.assertEqual(http.call_count, 2)


class NotionConformanceTests(unittest.TestCase):
    CFG = {"token": "notion-test"}

    @staticmethod
    def _page(edited="2026-08-19T10:00:00Z"):
        return {
            "object": "page", "id": "page1", "last_edited_time": edited,
            "properties": {"Name": {"type": "title", "title": [
                {"plain_text": "Runbook"},
            ]}},
        }

    def test_capped_poll_is_typed_acl_scoped_and_does_not_advance_cursor(self) -> None:
        old_cursor = "2026-08-18T00:00:00Z"

        def api(method, path, token, body=None):
            if path == "/v1/search":
                return {"results": [self._page()], "has_more": True,
                        "next_cursor": "next-page"}
            return {"results": [], "has_more": False, "next_cursor": None}

        with patch.object(notion, "MAX_PAGES_PER_SYNC", 1), \
             patch.object(notion, "_api", side_effect=api):
            result = notion.list_items(self.CFG, old_cursor)
        self.assertIsInstance(result, PollResult)
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, old_cursor)
        self.assertEqual(result.checkpoint, "next-page")
        self.assertEqual(result.tombstones, [])
        self.assertEqual(result.items[0]["acl"], ACLMetadata("connector_scope"))

    def test_http_boundary_retries_rate_limit_using_retry_after(self) -> None:
        response = notion._net.Response
        with patch.object(notion, "call_with_retry", side_effect=_retry_without_sleep), \
             patch.object(notion._net, "fetch", side_effect=[
                 response(429, b'{"message":"slow down"}', {"Retry-After": "0"},
                          "https://api.notion.com/v1/users/me", False),
                 response(200, b'{"object":"user"}', {},
                          "https://api.notion.com/v1/users/me", False),
             ]) as fetch:
            self.assertEqual(notion._api("GET", "/v1/users/me", "token")["object"],
                             "user")
        self.assertEqual(fetch.call_count, 2)


class _LiveConnectorCase:
    provider = None
    config = None

    def test_live_validation_and_poll_contract(self) -> None:
        error = self.provider.validate(self.config)
        self.assertIsNone(error, error)
        result = self.provider.list_items(self.config, None)
        self.assertIsInstance(result, PollResult)
        for item in result.items:
            acl = item.acl if hasattr(item, "acl") else item.get("acl")
            self.assertIsInstance(acl, ACLMetadata)


_LIVE = os.environ.get("MARI_TEST_LIVE_CONNECTORS") == "1"


@unittest.skipUnless(_LIVE and os.environ.get("MARI_AIRTABLE_PAT") and
                     os.environ.get("MARI_AIRTABLE_BASE_ID"),
                     "set MARI_TEST_LIVE_CONNECTORS=1 and Airtable sandbox credentials")
class LiveAirtableTests(_LiveConnectorCase, unittest.TestCase):
    provider = airtable
    config = {"pat": os.environ.get("MARI_AIRTABLE_PAT", ""),
              "base_id": os.environ.get("MARI_AIRTABLE_BASE_ID", "")}


@unittest.skipUnless(_LIVE and os.environ.get("MARI_ASANA_PAT"),
                     "set MARI_TEST_LIVE_CONNECTORS=1 and MARI_ASANA_PAT")
class LiveAsanaTests(_LiveConnectorCase, unittest.TestCase):
    provider = asana
    config = {"pat": os.environ.get("MARI_ASANA_PAT", ""),
              "workspace": os.environ.get("MARI_ASANA_WORKSPACE", "")}


@unittest.skipUnless(_LIVE and os.environ.get("MARI_TRELLO_API_KEY") and
                     os.environ.get("MARI_TRELLO_TOKEN"),
                     "set MARI_TEST_LIVE_CONNECTORS=1 and Trello sandbox credentials")
class LiveTrelloTests(_LiveConnectorCase, unittest.TestCase):
    provider = trello
    config = {"api_key": os.environ.get("MARI_TRELLO_API_KEY", ""),
              "token": os.environ.get("MARI_TRELLO_TOKEN", "")}


@unittest.skipUnless(_LIVE and os.environ.get("MARI_NOTION_TOKEN"),
                     "set MARI_TEST_LIVE_CONNECTORS=1 and MARI_NOTION_TOKEN")
class LiveNotionTests(_LiveConnectorCase, unittest.TestCase):
    provider = notion
    config = {"token": os.environ.get("MARI_NOTION_TOKEN", "")}


if __name__ == "__main__":
    unittest.main()

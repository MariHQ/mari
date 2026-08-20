from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.infrastructure import connector_runtime as connect_sync
import connectors
from connectors import confluence, dropbox, slack
from connectors._protocol import (
    ACLMetadata,
    ConnectorCallError,
    ErrorKind,
    PollItem,
    PollResult,
    adapt_poll_result,
    call_with_retry,
    classify_error,
)


class ProtocolConformanceTests(unittest.TestCase):
    def test_registry_modules_expose_the_contract(self) -> None:
        connectors.REGISTRY.refresh()
        self.assertGreater(len(connectors.REGISTRY), 0)
        for key, entry in connectors.REGISTRY.items():
            with self.subTest(provider=key):
                self.assertIsNone(entry.get("error"))
                provider = entry["provider"]
                self.assertEqual(provider["key"], key)
                self.assertTrue(provider.get("name"))
                self.assertIsInstance(provider.get("fields"), list)
                self.assertTrue(callable(entry["validate"]))
                self.assertTrue(callable(entry["list_items"]))

    def test_legacy_tuple_is_adapted_and_typed_result_still_unpacks(self) -> None:
        legacy = adapt_poll_result(([{"path": "one"}], "cursor"))
        self.assertTrue(legacy.snapshot_complete)
        self.assertEqual(legacy.cursor, "cursor")
        items, cursor = PollResult([{"path": "two"}], "next", tombstones=["gone"])
        self.assertEqual(items[0]["path"], "two")
        self.assertEqual(cursor, "next")

    def test_poll_item_keeps_acl_metadata_explicit(self) -> None:
        acl = ACLMetadata("restricted", ("group:engineering", "user:42"))
        item = PollItem("42", "Runbook", "body", acl=acl).as_dict()
        self.assertEqual(item["acl"], acl)
        self.assertNotEqual(item["acl"].visibility, "public")


class RetryConformanceTests(unittest.TestCase):
    def test_transient_failure_retries_with_bounded_backoff(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("network unavailable")
            return "ok"

        self.assertEqual(call_with_retry(flaky, sleep=sleeps.append), "ok")
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, [1, 2])

    def test_auth_and_permanent_failures_do_not_retry(self) -> None:
        for error in (ConnectorCallError("bad token", ErrorKind.AUTH),
                      RuntimeError("invalid request")):
            with self.subTest(error=str(error)):
                calls = 0

                def fail():
                    nonlocal calls
                    calls += 1
                    raise error

                with self.assertRaises(type(error)):
                    call_with_retry(fail, sleep=lambda _: None)
                self.assertEqual(calls, 1)

    def test_http_status_classification(self) -> None:
        class VendorError(Exception):
            def __init__(self, status):
                super().__init__(f"HTTP {status}")
                self.status = status

        self.assertEqual(classify_error(VendorError(401)), ErrorKind.AUTH)
        self.assertEqual(classify_error(VendorError(429)), ErrorKind.RATE_LIMIT)
        self.assertEqual(classify_error(VendorError(503)), ErrorKind.TRANSIENT)
        self.assertEqual(classify_error(RuntimeError("API error (HTTP 429): slow down")),
                         ErrorKind.RATE_LIMIT)
        self.assertEqual(classify_error(RuntimeError("API error (HTTP 502): upstream")),
                         ErrorKind.TRANSIENT)


class SnapshotSafetyTests(unittest.TestCase):
    ROWS = [
        {"id": 1, "source_path": "confluence/seen"},
        {"id": 2, "source_path": "confluence/missing"},
        {"id": 3, "source_path": "confluence/deleted"},
    ]

    def test_incomplete_full_snapshot_never_deletes_by_absence(self) -> None:
        gone = connect_sync.deletion_ids(
            self.ROWS, "confluence", {"seen"}, set(), full=True,
            snapshot_complete=False)
        self.assertEqual(gone, [])

    def test_complete_full_snapshot_reconciles_absence(self) -> None:
        gone = connect_sync.deletion_ids(
            self.ROWS, "confluence", {"seen"}, set(), full=True,
            snapshot_complete=True)
        self.assertEqual(gone, [2, 3])

    def test_incremental_tombstone_is_authoritative(self) -> None:
        gone = connect_sync.deletion_ids(
            self.ROWS, "confluence", set(), {"deleted"}, full=False,
            snapshot_complete=False)
        self.assertEqual(gone, [3])

    def test_confluence_marks_a_safety_capped_listing_incomplete(self) -> None:
        page = {"id": "42", "title": "Runbook", "body": {"storage": {"value": "body"}},
                "version": {"number": 1},
                "history": {"lastUpdated": {"when": "2026-08-19T10:00:00Z"}}}
        full_page = [page] * confluence.PAGE_SIZE
        with patch.object(confluence, "_get",
                          return_value={"results": full_page, "size": confluence.PAGE_SIZE}):
            result = confluence.list_items({}, "2026-08-18T00:00:00Z")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(len(result.items), confluence.PAGE_SIZE * confluence.MAX_PAGES)

    def test_slack_marks_channel_page_cap_incomplete(self) -> None:
        response = {"channels": [], "response_metadata": {"next_cursor": "more"}}
        with patch.object(slack, "_call", return_value=response):
            channels = slack._channels("token")
        self.assertFalse(channels.complete)

    def test_dropbox_emits_native_deleted_markers_as_tombstones(self) -> None:
        response = {"cursor": "next", "has_more": False, "entries": [
            {".tag": "deleted", "path_lower": "/gone.md"},
            {".tag": "folder", "path_lower": "/folder"},
        ]}
        with patch.object(dropbox, "_rpc", return_value=response):
            result = dropbox.list_items({"access_token": "x"}, "old")
        self.assertEqual(result.tombstones, ["gone.md"])
        self.assertEqual(result.cursor, "next")

    def test_dropbox_page_cap_returns_resumable_incomplete_checkpoint(self) -> None:
        first = {"cursor": "page-1", "has_more": True, "entries": []}
        with patch.object(dropbox, "MAX_PAGES", 1), patch.object(
                dropbox, "_rpc", return_value=first) as rpc:
            result = dropbox.list_items({"access_token": "x"}, "old")
        self.assertFalse(result.snapshot_complete)
        self.assertEqual(result.cursor, "old")
        self.assertEqual(result.checkpoint, "page-1")
        self.assertEqual(rpc.call_count, 1)


if __name__ == "__main__":
    unittest.main()

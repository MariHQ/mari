from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Request

import provider_events
from connectors import confluence


def request_for(payload: dict, headers: dict[str, str]) -> Request:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    normalized = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    normalized.append((b"content-length", str(len(raw)).encode()))
    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": normalized,
        "server": ("mari.example", 443), "client": ("127.0.0.1", 1),
    }
    return Request(scope, receive)


def signature(payload: dict, secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class GitHubEventTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "installation": {"id": 99},
            "repository": {"full_name": "acme/docs"},
            "ref": "refs/heads/main",
            "commits": [{"added": [f"docs/{index}.md" for index in range(600)]}],
        }
        self.headers = {
            "X-GitHub-Delivery": "delivery-1", "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature(self.payload, "secret"),
        }

    def test_signed_delivery_is_bounded_and_durable_before_ack(self):
        installation = {"id": 7, "project_id": 3, "config": {"webhook_secret": "secret"}}
        with patch.object(provider_events, "q1", side_effect=[installation, {"id": 41}]), \
             patch.object(provider_events.INBOX, "enqueue", return_value=(12, True)) as enqueue:
            result = asyncio.run(provider_events.github_webhook(request_for(self.payload, self.headers)))
        self.assertEqual(result, {"ok": True, "queued": True, "event_id": 12})
        provider, project, delivery, envelope = enqueue.call_args.args
        self.assertEqual((provider, project, delivery), ("github", 3, "delivery-1"))
        self.assertEqual(envelope["source_id"], 41)
        self.assertEqual(len(envelope["hint"]["paths"]), provider_events.MAX_DIRTY_PATHS)
        self.assertTrue(envelope["hint"]["paths_truncated"])
        self.assertNotIn("commits", envelope)

    def test_bad_signature_never_enqueues(self):
        headers = {**self.headers, "X-Hub-Signature-256": "sha256=bad"}
        with patch.object(provider_events, "q1", return_value={
                "id": 7, "project_id": 3, "config": {"webhook_secret": "secret"}}), \
             patch.object(provider_events.INBOX, "enqueue") as enqueue:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(provider_events.github_webhook(request_for(self.payload, headers)))
        self.assertEqual(raised.exception.status_code, 401)
        enqueue.assert_not_called()

    def test_storage_failure_is_not_acknowledged(self):
        installation = {"id": 7, "project_id": 3, "config": {"webhook_secret": "secret"}}
        with patch.object(provider_events, "q1", side_effect=[installation, {"id": 41}]), \
             patch.object(provider_events.INBOX, "enqueue", side_effect=OSError("db down")):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(provider_events.github_webhook(request_for(self.payload, self.headers)))
        self.assertEqual(raised.exception.status_code, 503)

    def test_worker_refetches_canonical_source_with_external_scope(self):
        source = {"id": 41, "project_id": 3, "project_slug": "acme", "project_name": "Acme",
                  "config": {"repo": "acme/docs"}}
        row = {"project_id": 3, "payload": {"installation_id": 7, "source_id": 41,
               "hint": {"repository": "acme/docs", "number": 8}}}
        with patch.object(provider_events, "q1", return_value={"id": 7}), \
             patch.object(provider_events, "_source", return_value=source), \
             patch.object(provider_events.ingest, "run_sync", return_value={}) as run:
            provider_events.process_github_delivery(row)
        run.assert_called_once_with(41, False)

    def test_worker_rejects_delivery_after_installation_disconnects(self):
        row = {"project_id": 3, "payload": {"installation_id": 7, "source_id": 41,
               "hint": {"repository": "acme/docs"}}}
        with patch.object(provider_events, "q1", return_value=None), \
             patch.object(provider_events.ingest, "run_sync") as run:
            with self.assertRaisesRegex(RuntimeError, "installation is no longer active"):
                provider_events.process_github_delivery(row)
        run.assert_not_called()


class ConfluenceEventTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"webhookEvent": "page_updated", "page": {
            "id": "123", "space": {"key": "ENG"}, "title": "untrusted"}}
        self.headers = {
            "X-Atlassian-Webhook-Identifier": "atl-1",
            "X-Mari-Signature-256": signature(self.payload, "secret"),
        }
        self.source = {"id": 8, "project_id": 3, "provider": "confluence",
                       "config": {"webhook_secret": "secret", "space_key": "ENG"}}

    def test_signed_page_hint_is_enqueued_without_untrusted_content(self):
        with patch.object(provider_events, "q1", return_value=self.source), \
             patch.object(provider_events.INBOX, "enqueue", return_value=(17, True)) as enqueue:
            result = asyncio.run(provider_events.confluence_webhook(
                8, request_for(self.payload, self.headers)))
        self.assertEqual(result["event_id"], 17)
        envelope = enqueue.call_args.args[3]
        self.assertEqual(envelope["hint"], {
            "event": "page_updated", "page_id": "123", "space_key": "ENG"})
        self.assertNotIn("title", envelope["hint"])

    def test_cross_space_event_is_rejected_before_enqueue(self):
        payload = {"event": "page_updated", "page": {
            "id": "123", "space": {"key": "HR"}}}
        headers = {**self.headers, "X-Mari-Signature-256": signature(payload, "secret")}
        with patch.object(provider_events, "q1", return_value=self.source), \
             patch.object(provider_events.INBOX, "enqueue") as enqueue:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(provider_events.confluence_webhook(8, request_for(payload, headers)))
        self.assertEqual(raised.exception.status_code, 403)
        enqueue.assert_not_called()

    def test_page_worker_uses_targeted_canonical_refetch(self):
        source = {**self.source, "project_slug": "acme", "project_name": "Acme"}
        row = {"project_id": 3, "payload": {"source_id": 8,
               "hint": {"event": "page_updated", "page_id": "123"}}}
        with patch.object(provider_events, "_source", return_value=source), \
             patch.object(provider_events, "_sync_confluence_page") as sync_page:
            provider_events.process_confluence_delivery(row)
        sync_page.assert_called_once_with(source, "123")

    def test_space_hint_uses_poll_reconciliation(self):
        source = {**self.source, "project_slug": "acme", "project_name": "Acme"}
        row = {"project_id": 3, "payload": {"source_id": 8,
               "hint": {"event": "space_updated", "space_key": "ENG"}}}
        with patch.object(provider_events, "_source", return_value=source), \
             patch.object(provider_events.ingest, "run_sync", return_value={}) as run:
            provider_events.process_confluence_delivery(row)
        run.assert_called_once_with(8, False)

    def test_fetch_page_turns_canonical_404_into_tombstone(self):
        with patch.object(confluence, "_get", side_effect=confluence.ConfluenceError("gone", 404)):
            self.assertIsNone(confluence.fetch_page({}, "123"))

    def test_fetch_page_returns_canonical_content_and_acl(self):
        page = {"id": "123", "type": "page", "title": "Canonical",
                "body": {"storage": {"value": "<p>Trusted</p>"}},
                "version": {"number": 4}, "space": {"key": "ENG"}}
        with patch.object(confluence, "_get", return_value=page):
            item = confluence.fetch_page({}, "123")
        self.assertEqual(item["body"], "Trusted")
        self.assertEqual(item["hash_hint"], "4")
        self.assertEqual(item["space_key"], "ENG")
        self.assertEqual(item["acl"].visibility, "connector_scope")

    def test_targeted_tombstone_deletes_only_the_routed_source_document(self):
        source = {"id": 8, "project_id": 3, "config": {
            "cursor": "durable-poll-cursor", "item_hashes": {"123": "3", "456": "2"}}}
        conn = _PageSyncConn(document_rows=[{"id": 91}], count=1)
        with patch.object(confluence, "fetch_page", return_value=None), \
             patch.object(provider_events.ingest, "_conn", return_value=_Context(conn)), \
             patch.object(provider_events.ingest, "_delete_documents") as delete:
            provider_events._sync_confluence_page(source, "123")
        delete.assert_called_once_with(conn, [91])
        update = next(args for sql, args in conn.calls if "UPDATE sources SET config" in sql)
        saved = json.loads(update[0])
        self.assertEqual(saved["cursor"], "durable-poll-cursor")
        self.assertNotIn("123", saved["item_hashes"])
        self.assertEqual(saved["item_hashes"]["456"], "2")

    def test_targeted_update_indexes_only_canonical_page(self):
        source = {"id": 8, "project_id": 3, "config": {
            "cursor": "durable-poll-cursor", "item_hashes": {}}}
        canonical = {"path": "123", "title": "Canonical", "body": "Trusted",
                     "hash_hint": "9", "space_key": "ENG"}
        conn = _PageSyncConn(count=1)
        with patch.object(confluence, "fetch_page", return_value=canonical), \
             patch.object(provider_events.ingest, "_conn", return_value=_Context(conn)), \
             patch.object(provider_events.ingest, "_upsert_document", return_value=(91, True)) as upsert, \
             patch.object(provider_events.ingest, "_chunk_settings", return_value=(100, 10)), \
             patch.object(provider_events.ingest, "_sync_chunks") as chunks:
            provider_events._sync_confluence_page(source, "123")
        self.assertEqual(upsert.call_args.args[3:5], ("Canonical", "Trusted"))
        chunks.assert_called_once_with(conn, 91, "Canonical", "Trusted", 100, 10)
        update = next(args for sql, args in conn.calls if "UPDATE sources SET config" in sql)
        saved = json.loads(update[0])
        self.assertEqual(saved["cursor"], "durable-poll-cursor")
        self.assertEqual(saved["item_hashes"]["123"], "9")


class _Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class _Result:
    def __init__(self, *, rows=None, one=None):
        self.rows = rows or []
        self.one = one

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


class _PageSyncConn:
    def __init__(self, *, document_rows=None, count=0):
        self.document_rows = document_rows or []
        self.count = count
        self.calls = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        if "SELECT id FROM documents" in sql:
            return _Result(rows=self.document_rows)
        if "SELECT count(*) AS n" in sql:
            return _Result(one={"n": self.count})
        return _Result()

    def commit(self):
        return None


if __name__ == "__main__":
    unittest.main()

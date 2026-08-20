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


if __name__ == "__main__":
    unittest.main()

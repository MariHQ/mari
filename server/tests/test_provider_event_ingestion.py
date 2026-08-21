from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Request

from mari_server.sources import provider_events
from mari_components import KnowledgeDocument


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
        with patch.object(provider_events.event_store, "github_installation", return_value=installation), \
             patch.object(provider_events.event_store, "github_source", return_value={"id": 41}), \
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
        with patch.object(provider_events.event_store, "github_installation", return_value={
                "id": 7, "project_id": 3, "config": {"webhook_secret": "secret"}}), \
             patch.object(provider_events.INBOX, "enqueue") as enqueue:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(provider_events.github_webhook(request_for(self.payload, headers)))
        self.assertEqual(raised.exception.status_code, 401)
        enqueue.assert_not_called()

    def test_storage_failure_is_not_acknowledged(self):
        installation = {"id": 7, "project_id": 3, "config": {"webhook_secret": "secret"}}
        with patch.object(provider_events.event_store, "github_installation", return_value=installation), \
             patch.object(provider_events.event_store, "github_source", return_value={"id": 41}), \
             patch.object(provider_events.INBOX, "enqueue", side_effect=OSError("db down")):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(provider_events.github_webhook(request_for(self.payload, self.headers)))
        self.assertEqual(raised.exception.status_code, 503)

    def test_repository_webhook_routes_without_a_github_app_installation(self):
        payload = {"repository": {"full_name": "acme/docs"}, "action": "edited"}
        headers = {
            "X-GitHub-Delivery": "delivery-repo", "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature(payload, "project-secret"),
        }
        source = {"id": 41, "project_id": 3,
                  "webhook_config": {"webhook_secret": "project-secret"}}
        with patch.object(provider_events.event_store, "github_webhook_sources", return_value=[source]), \
             patch.object(provider_events.INBOX, "enqueue", return_value=(19, True)) as enqueue:
            result = asyncio.run(provider_events.github_webhook(request_for(payload, headers)))
        self.assertEqual(result["event_id"], 19)
        self.assertEqual(enqueue.call_args.args[:3], ("github", 3, "delivery-repo"))

    def test_worker_refetches_canonical_source_with_external_scope(self):
        source = {"id": 41, "project_id": 3, "project_slug": "acme", "project_name": "Acme",
                  "config": {"repo": "acme/docs"}}
        row = {"project_id": 3, "payload": {"installation_id": 7, "source_id": 41,
               "hint": {"repository": "acme/docs", "number": 8}}}
        with patch.object(provider_events.event_store, "installation_active", return_value=True), \
             patch.object(provider_events, "_source", return_value=source), \
             patch.object(provider_events.ingest, "run_sync", return_value={}) as run, \
             patch.object(provider_events.event_store, "mark_github_delivery"):
            provider_events.process_github_delivery(row)
        run.assert_called_once_with(41, False)

    def test_pull_request_mention_runs_fact_validation_before_reconciliation(self):
        source = {"id": 41, "project_id": 3, "project_slug": "acme", "project_name": "Acme",
                  "config": {"repo": "acme/docs", "token": "token"}}
        row = {"project_id": 3, "delivery_id": "mention-1", "payload": {
            "installation_id": 0, "source_id": 41, "bot_login": "mari",
            "hint": {"repository": "acme/docs", "number": 8, "is_pull_request": True,
                     "comment_body": "@Mari validate facts", "comment_author_type": "User"}}}
        from mari_server.knowledge import service
        with patch.object(provider_events, "_source", return_value=source), \
             patch.object(service, "validate_github_pull_request") as validate, \
             patch.object(provider_events.ingest, "run_sync", return_value={}), \
             patch.object(provider_events.event_store, "mark_github_delivery"):
            provider_events.process_github_delivery(row)
        validate.assert_called_once_with(source, 8, "mention-1")

    def test_worker_rejects_delivery_after_installation_disconnects(self):
        row = {"project_id": 3, "payload": {"installation_id": 7, "source_id": 41,
               "hint": {"repository": "acme/docs"}}}
        with patch.object(provider_events.event_store, "installation_active", return_value=False), \
             patch.object(provider_events.ingest, "run_sync") as run:
            with self.assertRaisesRegex(RuntimeError, "installation is no longer active"):
                provider_events.process_github_delivery(row)
        run.assert_not_called()

    def test_fact_validation_posts_an_honest_result_when_no_facts_exist(self):
        from mari_server.knowledge import service
        source = {"config": {"repo": "acme/docs", "token": "token"}}
        with patch.object(service, "github_issue_comments", return_value=()), \
             patch.object(service, "github_pull_request", return_value={
                 "number": 8, "title": "Update docs", "body": "New text", "updated_at": "2026-08-21T00:00:00Z"}), \
             patch.object(service, "github_pull_files", return_value=()), \
             patch.object(service.knowledge_store, "fact_claims", return_value=set()), \
             patch.object(service, "post_github_comment") as post, \
             patch.object(service, "audit"):
            service.validate_github_pull_request(source, 8, "delivery-8")
        body = post.call_args.args[1]
        self.assertIn("No verified workspace facts", body)
        self.assertIn("mari-fact-validation:delivery-8", body)


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
        with patch.object(provider_events.event_store, "confluence_source", return_value=self.source), \
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
        with patch.object(provider_events.event_store, "confluence_source", return_value=self.source), \
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

    def test_targeted_tombstone_deletes_only_the_routed_source_document(self):
        source = {"id": 8, "project_id": 3, "config": {
            "site_url": "https://example.atlassian.net", "email": "a@example.test",
            "api_token": "token", "cursor": "durable-poll-cursor",
            "item_hashes": {"123": "3", "456": "2"}}}
        conn = MagicMock()
        with patch.object(provider_events, "fetch_confluence_page", return_value=None), \
             patch.object(provider_events.document_index, "connection", return_value=_Context(conn)), \
             patch.object(provider_events.document_repository, "ids_for_source_path", return_value=[91]), \
             patch.object(provider_events.document_index, "delete_documents") as delete, \
             patch.object(provider_events.document_repository, "finalize_source") as finalize:
            provider_events._sync_confluence_page(source, "123")
        delete.assert_called_once_with(conn, [91])
        saved = finalize.call_args.args[3]
        self.assertEqual(saved["cursor"], "durable-poll-cursor")
        self.assertNotIn("123", saved["item_hashes"])
        self.assertEqual(saved["item_hashes"]["456"], "2")

    def test_targeted_update_indexes_only_canonical_page(self):
        source = {"id": 8, "project_id": 3, "config": {
            "site_url": "https://example.atlassian.net", "email": "a@example.test",
            "api_token": "token", "cursor": "durable-poll-cursor", "item_hashes": {}}}
        canonical = KnowledgeDocument(
            "123", "Canonical", "Trusted", revision="9", metadata={"space_key": "ENG"},
        )
        conn = MagicMock()
        with patch.object(provider_events, "fetch_confluence_page", return_value=canonical), \
             patch.object(provider_events.document_index, "connection", return_value=_Context(conn)), \
             patch.object(provider_events.document_index, "upsert_document", return_value=(91, True)) as upsert, \
             patch.object(provider_events.document_index, "chunk_settings", return_value=(100, 10)), \
             patch.object(provider_events.document_index, "sync_chunks") as chunks, \
             patch.object(provider_events.document_repository, "finalize_source") as finalize:
            provider_events._sync_confluence_page(source, "123")
        self.assertEqual(upsert.call_args.args[3:5], ("Canonical", "Trusted"))
        chunks.assert_called_once_with(conn, 91, "Canonical", "Trusted", 100, 10)
        saved = finalize.call_args.args[3]
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

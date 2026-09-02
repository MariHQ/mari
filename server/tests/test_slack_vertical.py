from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi import HTTPException

from mari_server.identity import access
from mari_server.destinations import slack as bots
from mari_server.search import service as search_service


class Result:
    def __init__(self, one=None): self.one = one
    def fetchone(self): return self.one


class InstallationDatabase:
    """Transaction-shaped fake for the narrow installation persistence seam."""
    def __init__(self):
        self.installation = None
        self.calls = []

    def configure_slack(self, project_id, team_id, config):
        row = self.installation
        if row and row["external_team_id"] == team_id and row["project_id"] != project_id:
            raise ValueError("That Slack workspace is already connected to another project.")
        if row and row["project_id"] == project_id:
            row["external_team_id"] = team_id
            row["config"].update(config)
            self.calls.append(("update", project_id))
        else:
            self.installation = {"id": 5, "project_id": project_id, "provider": "slack",
                                 "external_team_id": team_id, "config": dict(config),
                                 "status": "connected"}
            self.calls.append(("insert", project_id))
        return self.installation["id"]

    def __enter__(self): return self
    def __exit__(self, *_): return False

    def execute(self, sql, args=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, args))
        if normalized.startswith("SELECT id, external_team_id"):
            row = self.installation
            return Result({"id": row["id"], "external_team_id": row["external_team_id"]}
                          if row and row["project_id"] == args[0] else None)
        if normalized.startswith("SELECT id, project_id"):
            row = self.installation
            return Result({"id": row["id"], "project_id": row["project_id"]}
                          if row and row["external_team_id"] == args[0] else None)
        if normalized.startswith("INSERT INTO bot_installations"):
            project_id, team_id, raw_config = args
            self.installation = {"id": 5, "project_id": project_id, "provider": "slack",
                                 "external_team_id": team_id, "config": json.loads(raw_config),
                                 "status": "connected"}
            return Result({"id": 5})
        if normalized.startswith("UPDATE bot_installations"):
            team_id, raw_config, installation_id, project_id = args
            assert self.installation and self.installation["id"] == installation_id
            assert self.installation["project_id"] == project_id
            self.installation["external_team_id"] = team_id
            self.installation["config"].update(json.loads(raw_config))
            return Result({"id": installation_id})
        return Result()


class FakeSlackHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        self.__class__.calls.append({"path": self.path,
                                     "authorization": self.headers.get("Authorization"), "body": body})
        if self.path == "/api/auth.test":
            payload = ({"ok": True, "team": "Acme", "team_id": "T-ACME", "user": "mari"}
                       if self.headers.get("Authorization") == "Bearer xoxb-valid"
                       else {"ok": False, "error": "invalid_auth"})
        elif self.path == "/api/chat.postMessage":
            payload = {"ok": True, "ts": "posted.1"}
        elif self.path == "/api/chat.update":
            payload = {"ok": True, "ts": body["ts"], "text": body["text"]}
        elif self.path == "/api/conversations.replies":
            payload = {"ok": True, "messages": [
                {"type": "message", "user": "U1", "text": "deploy?", "ts": body["ts"]},
                {"type": "message", "user": "U2", "text": "use the production checklist", "ts": "9.2"},
            ]}
        elif self.path == "/api/conversations.history":
            payload = {"ok": True, "messages": [
                {"type": "message", "bot_id": "B1",
                 "text": "Use the production checklist [3].", "ts": body["latest"]},
            ]}
        else:
            payload = {"ok": False, "error": "unknown_method"}
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


class MemoryInbox:
    def __init__(self):
        self.rows = []
        self.keys = set()
    def enqueue(self, provider, project_id, delivery_id, payload, *, coalesce_key=""):
        key = (provider, project_id, delivery_id)
        if key in self.keys:
            row = next(row for row in self.rows if row["key"] == key)
            return row["id"], False
        self.keys.add(key)
        row = {"id": len(self.rows) + 1, "key": key, "provider": provider,
               "project_id": project_id, "delivery_id": delivery_id,
               "payload": payload, "coalesce_key": coalesce_key, "attempts": 0,
               "status": "pending"}
        self.rows.append(row)
        return row["id"], True
    def claim(self):
        row = next((row for row in self.rows if row["status"] == "pending"), None)
        if row:
            row["status"] = "processing"
            row["attempts"] += 1
        return row
    def complete(self, row_id):
        self.rows[row_id - 1]["status"] = "completed"
    def retry(self, row_id, _error, _attempts):
        self.rows[row_id - 1]["status"] = "pending"


class SlackSetupToAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeSlackHandler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.slack_api = f"http://127.0.0.1:{cls.http.server_port}/api"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        FakeSlackHandler.calls.clear()
        search_service._rank_cache.clear()
        search_service._vec_cache.clear()
        self.database = InstallationDatabase()
        self.project = access.AccessContext(
            1, 7, "acme", "Acme", "admin", access.CAPABILITIES, principal_id="1")

    def _setup(self):
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.bot_store, "configure_slack", side_effect=self.database.configure_slack):
            return bots.slack_setup(bots.SlackSetupIn(
                bot_token=" xoxb-valid ", signing_secret=" signing-secret "), self.project)

    @staticmethod
    def _request(payload: dict, secret: str = "signing-secret"):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = "v0=" + hmac.new(
            secret.encode(), f"v0:{timestamp}:".encode() + raw, hashlib.sha256).hexdigest()

        class Request:
            headers = {"X-Slack-Request-Timestamp": timestamp,
                       "X-Slack-Signature": signature}
            async def body(self): return raw
        return Request()

    def _installed_row(self):
        row = dict(self.database.installation)
        row.update({"project_slug": "acme", "project_name": "Acme"})
        return row

    def test_invalid_auth_test_never_persists_credentials(self):
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.bot_store, "configure_slack", side_effect=self.database.configure_slack), \
             self.assertRaisesRegex(HTTPException, "invalid_auth"):
            bots.slack_setup(bots.SlackSetupIn(
                bot_token="xoxb-rejected", signing_secret="signing-secret"), self.project)
        self.assertIsNone(self.database.installation)
        self.assertFalse(any("bot_installations" in sql for sql, _ in self.database.calls))

    def test_verified_workspace_cannot_be_claimed_by_another_project(self):
        self.database.installation = {"id": 8, "project_id": 9, "provider": "slack",
                                      "external_team_id": "T-ACME", "config": {},
                                      "status": "connected"}
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.bot_store, "configure_slack", side_effect=self.database.configure_slack), \
             self.assertRaisesRegex(HTTPException, "another project") as error:
            bots.slack_setup(bots.SlackSetupIn(
                bot_token="xoxb-valid", signing_secret="signing-secret"), self.project)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.database.calls, [])

    def test_resaving_credentials_rotates_the_existing_project_installation(self):
        first = self._setup()
        second = self._setup()
        self.assertEqual(first["installationId"], second["installationId"])
        self.assertEqual([action for action, _project in self.database.calls], ["insert", "update"])

    def test_setup_routes_mentions_and_dms_to_allowed_project_knowledge_exactly_once(self):
        setup = self._setup()
        self.assertEqual(setup["teamId"], "T-ACME")
        self.assertEqual(self.database.installation["external_team_id"], "T-ACME")
        self.assertEqual(self.database.installation["config"]["bot_token"], "xoxb-valid")

        documents = [
            {"id": 1, "source": "slack", "title": "Allowed runbook", "snippet": "deploy safely",
             "body": "Use the allowed deploy process", "author": "", "author_initials": "",
             "updated_src": None, "kind": "page", "tags": [], "boost": 1,
             "acl_visibility": "restricted", "acl_principals": ["channel:C-ALLOWED"]},
            {"id": 2, "source": "slack", "title": "Forbidden plan", "snippet": "deploy secretly",
             "body": "Never reveal this", "author": "", "author_initials": "",
             "updated_src": None, "kind": "page", "tags": [], "boost": 1,
             "acl_visibility": "restricted", "acl_principals": ["channel:C-OTHER"]},
        ]
        prompts = []
        mention = {"type": "event_callback", "team_id": "T-ACME", "event_id": "Ev-mention",
                   "event": {"type": "app_mention", "text": "<@B> deploy?",
                             "channel": "C-ALLOWED", "ts": "1.0"}}
        dm = {"type": "event_callback", "team_id": "T-ACME", "event_id": "Ev-dm",
              "event": {"type": "message", "channel_type": "im", "text": "deploy?",
                        "channel": "C-ALLOWED", "ts": "2.0"}}
        inbox = MemoryInbox()
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.bot_store, "installation_by_team", return_value=self._installed_row()), \
             patch.object(bots.bot_store, "installation", return_value=self._installed_row()), \
             patch.object(bots.bot_store, "save_thread"), \
             patch.object(bots.bot_store, "touch_installation"), \
             patch.object(bots.bot_store, "verified_facts", return_value=[]), \
             patch.object(bots.bot_store, "log_usage"), \
             patch.object(bots.trajectory_store, "record_external_observation"), \
             patch.object(bots, "_EVENT_INBOX", inbox), \
             patch.object(bots, "_refresh_slack_aggregate"), \
             patch.object(search_service.search_store, "keyword_candidates", return_value=documents), \
             patch.object(search_service.search_store, "documents_by_id",
                          side_effect=lambda _project_id, ids: [dict(d) for d in documents if d["id"] in ids]), \
             patch.object(search_service.llm, "embed", return_value=None), \
             patch.object(bots.llm, "chat_stream",
                          side_effect=lambda messages, _system: prompts.append(messages[-1]["content"]) or iter(["Use the runbook [1]."])):
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(mention))), {"ok": True})
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(dm))), {"ok": True})
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(mention))),
                             {"ok": True, "duplicate": True})
            # HTTP ACK means durable acceptance, not in-process completion.
            self.assertFalse([call for call in FakeSlackHandler.calls
                              if call["path"] == "/api/chat.postMessage"])
            dispatcher = bots.EventDispatcher(inbox, {"slack": bots._process_slack_delivery})
            self.assertTrue(dispatcher.drain_once())
            self.assertTrue(dispatcher.drain_once())

        posts = [call for call in FakeSlackHandler.calls if call["path"] == "/api/chat.postMessage"]
        self.assertEqual(len(posts), 2)
        self.assertTrue(all(call["authorization"] == "Bearer xoxb-valid" for call in posts))
        self.assertNotIn("thread_ts", posts[0]["body"])
        self.assertNotIn("thread_ts", posts[1]["body"])
        self.assertTrue(all("Allowed runbook" in prompt for prompt in prompts))
        self.assertTrue(all("use the production checklist" not in prompt for prompt in prompts))
        self.assertTrue(all("deploy?" in prompt for prompt in prompts))
        self.assertFalse([call for call in FakeSlackHandler.calls
                          if call["path"] == "/api/conversations.replies"])
        self.assertTrue(all("Forbidden plan" not in prompt and "Never reveal this" not in prompt
                            for prompt in prompts))

    def test_unmentioned_followup_routes_from_maris_posted_response(self):
        inbox = MemoryInbox()
        joined = set()
        installation = self._setup()
        row = self._installed_row()

        mention = {"type": "event_callback", "team_id": "T-ACME", "event_id": "Ev-root",
                   "event": {"type": "app_mention", "text": "<@B> start", "channel": "C1", "ts": "10.0"}}
        early = {"type": "event_callback", "team_id": "T-ACME", "event_id": "Ev-early",
                    "event": {"type": "message", "text": "and production?", "channel": "C1",
                              "thread_ts": "10.0", "ts": "10.1"}}
        followup = {"type": "event_callback", "team_id": "T-ACME", "event_id": "Ev-reply",
                    "event": {"type": "message", "text": "and production?", "channel": "C1",
                              "thread_ts": "posted.1", "ts": "10.2"}}
        unrelated = {"type": "event_callback", "team_id": "T-ACME", "event_id": "Ev-other",
                     "event": {"type": "message", "text": "private conversation", "channel": "C1",
                               "thread_ts": "99.0", "ts": "99.1"}}
        with patch.object(bots.bot_store, "installation_by_team", return_value=row), \
             patch.object(bots.bot_store, "thread_exists",
                          side_effect=lambda installation_id, _project_id, channel, thread:
                          (installation_id, channel, thread) in joined), \
             patch.object(bots, "_EVENT_INBOX", inbox):
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(mention))), {"ok": True})
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(early))), {"ok": True})
            joined.add((row["id"], "C1", "posted.1"))
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(followup))), {"ok": True})
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(unrelated))), {"ok": True})
        self.assertEqual([item["delivery_id"] for item in inbox.rows], ["Ev-root", "Ev-reply"])
        self.assertEqual(inbox.rows[0]["coalesce_key"], "5:C1:10.0")
        self.assertEqual(inbox.rows[1]["coalesce_key"], "5:C1:posted.1")
        self.assertEqual(installation["installationId"], row["id"])

    def test_thread_followup_loads_context_and_replies_in_the_same_thread(self):
        self._setup()
        row = {
            "project_id": 7,
            "delivery_id": "Ev-followup",
            "payload": {
                "installation_id": 5,
                "event": {"type": "message", "text": "and production?", "channel": "C1",
                          "thread_ts": "posted.1", "ts": "posted.2"},
            },
        }
        installation = self._installed_row()
        answered = []
        saved_threads = []
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.bot_store, "installation", return_value=installation), \
             patch.object(bots.bot_store, "thread", return_value=None), \
             patch.object(bots.bot_store, "save_thread",
                          side_effect=lambda *args: saved_threads.append(args)), \
             patch.object(bots.bot_store, "touch_installation"), \
             patch.object(bots.bot_store, "log_usage"), \
             patch.object(bots.trajectory_store, "record_external_observation"), \
             patch.object(bots, "_refresh_slack_aggregate") as refresh, \
             patch.object(bots, "stream_answer_question",
                          side_effect=lambda question, context, **_kwargs: answered.append((question, context)) or iter(["Production is ready [1]."])):
            bots._process_slack_delivery(row)

        history = [call for call in FakeSlackHandler.calls
                   if call["path"] == "/api/conversations.history"]
        posts = [call for call in FakeSlackHandler.calls
                 if call["path"] == "/api/chat.postMessage"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["body"]["latest"], "posted.1")
        self.assertEqual(history[0]["body"]["limit"], 15)
        self.assertEqual(answered[0][0], "and production?")
        self.assertIn("Use the production checklist [3].", answered[0][1])
        self.assertNotIn("and production?", answered[0][1])
        self.assertEqual(posts[0]["body"]["thread_ts"], "posted.1")
        refresh.assert_called_once_with(7, "xoxb-valid", "C1", "posted.1")
        self.assertTrue(any(args[3] == "posted.1" for args in saved_threads))

    def test_top_level_mention_has_no_synthetic_thread_context(self):
        self._setup()
        row = {
            "project_id": 7,
            "delivery_id": "Ev-cached-root",
            "payload": {
                "installation_id": 5,
                "event": {"type": "app_mention", "text": "<@B> tell me about mari",
                          "channel": "C1", "ts": "20.0"},
            },
        }
        answered = []
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.bot_store, "installation", return_value=self._installed_row()), \
             patch.object(bots.bot_store, "save_thread"), \
             patch.object(bots.bot_store, "touch_installation"), \
             patch.object(bots.bot_store, "log_usage"), \
             patch.object(bots.trajectory_store, "record_external_observation") as observe, \
             patch.object(bots, "_refresh_slack_aggregate"), \
             patch.object(bots, "stream_answer_question",
                          side_effect=lambda question, context, observe=None: (
                              observe({"id": 14}, [], "cache") if observe else None,
                              answered.append((question, context)),
                              iter(["Cached Mari answer."]),
                          )[-1]):
            bots._process_slack_delivery(row)

        self.assertEqual(answered, [("tell me about mari", "")])
        observe.assert_called_once()



class SlackThreadReingestTests(unittest.TestCase):
    """The bot re-ingests the thread it answered in. It used to write the
    whole sources.config back from a copy read before the upsert, undoing a
    concurrent sweep's cursor, checkpoint and snapshot flags, and stored the
    raw revision as the hash so the next poll re-chunked the thread."""

    def _document(self):
        from mari_components import KnowledgeDocument
        return KnowledgeDocument("C1:1700000000.000100", "Deploy thread", "Production is ready.",
                                 revision="1700000000.000200", updated_at="2026-09-01T00:00:00Z")

    def test_reingest_merges_one_hash_entry_on_the_document_connection(self):
        from mari_components.sync import document_fingerprint
        document = self._document()
        conn = unittest.mock.MagicMock()
        conn.__enter__.return_value = conn
        order = []
        conn.commit.side_effect = lambda: order.append("commit")
        sources = [{"id": 5, "config": {"token": "xoxb", "cursor": "sweep-cursor",
                                        "checkpoint": "ckpt-9", "full_snapshot_pending": True,
                                        "item_hashes": {"other": "h"}}}]
        with patch.object(bots.bot_store, "slack_sources", return_value=sources), \
             patch.object(bots, "fetch_slack_thread_by_id", return_value=(document, True)), \
             patch.object(bots.document_index, "chunk_settings", return_value=(100, 10)), \
             patch.object(bots.document_index, "connection", return_value=conn), \
             patch.object(bots.document_index, "upsert_document", return_value=(31, False)) as upsert, \
             patch.object(bots.document_index, "sync_chunks"), \
             patch.object(bots.connector_sync, "merge_config",
                          side_effect=lambda *a, **k: order.append(("merge", a, k))) as merge, \
             patch.object(bots, "invalidate_search"):
            bots._refresh_slack_aggregate(7, "xoxb", "C1", "1700000000.000100")

        expected = document_fingerprint(document)
        self.assertNotEqual(expected, document.revision)
        self.assertEqual(upsert.call_args.args[7], expected)
        merge.assert_called_once_with(conn, 5, {}, hashes={document.external_id: expected})
        # one entry, merged under the row lock, in the same transaction as the document
        self.assertEqual([step if isinstance(step, str) else step[0] for step in order],
                         ["merge", "commit"])
        self.assertFalse(hasattr(bots.bot_store, "save_source_config"))
        # the sweep's keys were never read back and cannot be put back
        self.assertEqual(sources[0]["config"]["cursor"], "sweep-cursor")

    def test_a_source_paused_under_the_row_lock_is_rolled_back_not_revived(self):
        document = self._document()
        conn = unittest.mock.MagicMock()
        conn.__enter__.return_value = conn
        with patch.object(bots.bot_store, "slack_sources", return_value=[{"id": 5, "config": {}}]), \
             patch.object(bots, "fetch_slack_thread_by_id", return_value=(document, True)), \
             patch.object(bots.document_index, "chunk_settings", return_value=(100, 10)), \
             patch.object(bots.document_index, "connection", return_value=conn), \
             patch.object(bots.document_index, "upsert_document", return_value=(31, False)), \
             patch.object(bots.document_index, "sync_chunks"), \
             patch.object(bots.connector_sync, "merge_config",
                          side_effect=bots.connector_sync.SourcePaused("source is paused")), \
             patch.object(bots, "invalidate_search") as invalidate:
            bots._refresh_slack_aggregate(7, "xoxb", "C1", "1700000000.000100")
        conn.rollback.assert_called_once_with()
        conn.commit.assert_not_called()
        invalidate.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()

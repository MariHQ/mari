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

import access
import bots
import queries


class Result:
    def __init__(self, one=None): self.one = one
    def fetchone(self): return self.one


class InstallationDatabase:
    """Transaction-shaped fake for the narrow installation persistence seam."""
    def __init__(self):
        self.installation = None
        self.calls = []

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


class InlineExecutor:
    def submit(self, function, *args):
        function(*args)
        return True


class MemoryLedger:
    def __init__(self): self.claimed = set()
    def claim(self, provider, event_id):
        key = (provider, event_id)
        if key in self.claimed: return False
        self.claimed.add(key)
        return True
    def complete(self, _provider, _event_id): pass
    def release(self, provider, event_id): self.claimed.discard((provider, event_id))


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
        queries._rank_cache.clear()
        queries._vec_cache.clear()
        self.database = InstallationDatabase()
        self.project = access.AccessContext(
            1, 7, "acme", "Acme", "admin", access.CAPABILITIES, principal_id="1")

    def _setup(self):
        with access.use_access(self.project), patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.auth, "_conn", return_value=self.database):
            return bots.slack_setup(bots.SlackSetupIn(
                bot_token=" xoxb-valid ", signing_secret=" signing-secret "))

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
        with access.use_access(self.project), patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.auth, "_conn", return_value=self.database), \
             self.assertRaisesRegex(HTTPException, "invalid_auth"):
            bots.slack_setup(bots.SlackSetupIn(
                bot_token="xoxb-rejected", signing_secret="signing-secret"))
        self.assertIsNone(self.database.installation)
        self.assertFalse(any("bot_installations" in sql for sql, _ in self.database.calls))

    def test_verified_workspace_cannot_be_claimed_by_another_project(self):
        self.database.installation = {"id": 8, "project_id": 9, "provider": "slack",
                                      "external_team_id": "T-ACME", "config": {},
                                      "status": "connected"}
        with access.use_access(self.project), patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots.auth, "_conn", return_value=self.database), \
             self.assertRaisesRegex(HTTPException, "another project") as error:
            bots.slack_setup(bots.SlackSetupIn(
                bot_token="xoxb-valid", signing_secret="signing-secret"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(any(sql.startswith("UPDATE bot_installations") for sql, _ in self.database.calls))

    def test_resaving_credentials_rotates_the_existing_project_installation(self):
        first = self._setup()
        second = self._setup()
        self.assertEqual(first["installationId"], second["installationId"])
        statements = [sql for sql, _ in self.database.calls]
        self.assertEqual(sum(sql.startswith("INSERT INTO bot_installations") for sql in statements), 1)
        self.assertEqual(sum(sql.startswith("UPDATE bot_installations") for sql in statements), 1)

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
        with patch.object(bots, "SLACK_API", self.slack_api), \
             patch.object(bots, "q1", return_value=self._installed_row()), \
             patch.object(bots, "exec_"), patch.object(bots, "pq", return_value=[]), \
             patch.object(bots, "_SLACK_EXECUTOR", InlineExecutor()), \
             patch.object(bots, "_SLACK_EVENTS", MemoryLedger()), \
             patch.object(queries, "q", return_value=documents), \
             patch.object(queries.llm, "embed", return_value=None), \
             patch.object(bots.llm, "generate", side_effect=lambda prompt, _system: prompts.append(prompt) or "Use the runbook [1]."):
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(mention))), {"ok": True})
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(dm))), {"ok": True})
            self.assertEqual(asyncio.run(bots.slack_webhook(self._request(mention))),
                             {"ok": True, "duplicate": True})

        posts = [call for call in FakeSlackHandler.calls if call["path"] == "/api/chat.postMessage"]
        self.assertEqual(len(posts), 2)
        self.assertTrue(all(call["authorization"] == "Bearer xoxb-valid" for call in posts))
        self.assertEqual(posts[0]["body"]["thread_ts"], "1.0")
        self.assertIsNone(posts[1]["body"]["thread_ts"])
        self.assertTrue(all("Allowed runbook" in prompt for prompt in prompts))
        self.assertTrue(all("Forbidden plan" not in prompt and "Never reveal this" not in prompt
                            for prompt in prompts))


if __name__ == "__main__":
    unittest.main()

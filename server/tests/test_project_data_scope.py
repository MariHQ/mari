"""Two-project regression tests for caches and external principal routing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import access
import bots
import db
import mcp
import queries
import retrieval
import mutations_knowledge


def context(project_id: int, slug: str) -> access.AccessContext:
    return access.AccessContext(
        user_id=1, project_id=project_id, project_slug=slug,
        project_name=slug.title(), role="admin", capabilities=access.CAPABILITIES)


class ProjectDataScopeTests(unittest.TestCase):
    def tearDown(self):
        access.set_access(None)
        queries._vec_cache.clear()
        queries._rank_cache.clear()
        retrieval._INDEXES.clear()

    def test_project_db_helper_prepends_only_active_project(self):
        with access.use_access(context(7, "acme")), patch.object(db, "q", return_value=[]) as query:
            db.pq("SELECT id FROM documents WHERE project_id = %s AND title = %s", ("Runbook",))
        self.assertEqual(query.call_args.args[1], (7, "Runbook"))
        with self.assertRaises(RuntimeError):
            db.pq("SELECT 1 WHERE %s = %s", (1,))

    def test_core_knowledge_queries_always_bind_active_project(self):
        with access.use_access(context(7, "acme")), \
             patch.object(queries, "q", return_value=[]) as query:
            service = queries.Query()
            service.facts()
            service.tasks()
            service.glossary()
            service.approved_answers()
            service.decisions()
        self.assertEqual(len(query.call_args_list), 5)
        for call in query.call_args_list:
            self.assertIn("project_id", call.args[0])
            self.assertEqual(call.args[1][0], 7)

    def test_foreign_knowledge_ids_fail_closed_before_write(self):
        with access.use_access(context(7, "acme")), \
             patch.object(mutations_knowledge, "q1", return_value=None) as read, \
             patch.object(mutations_knowledge, "exec_") as write:
            self.assertFalse(mutations_knowledge.MutKnowledge().set_task_done(99, True))
            self.assertFalse(mutations_knowledge.MutKnowledge().ratify_decision(99))
            self.assertFalse(mutations_knowledge.MutKnowledge().set_answer_channels(99, ["slack"]))
        self.assertTrue(all(call.args[1][0] == 7 for call in read.call_args_list))
        write.assert_not_called()

    def test_embedding_and_rank_caches_do_not_cross_projects(self):
        rows = {
            7: [{"id": 1, "source": "docs", "title": "Acme only", "snippet": "deploy", "body": "",
                 "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [], "boost": 1}],
            9: [{"id": 2, "source": "docs", "title": "Beta only", "snippet": "deploy", "body": "",
                 "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [], "boost": 1}],
        }

        def scoped_rows(_sql, args=()):
            return [dict(row) for row in rows[int(args[0])]]

        with patch.object(queries.llm, "embed", return_value=None) as embed, \
             patch.object(queries, "q", side_effect=scoped_rows):
            with access.use_access(context(7, "acme")):
                acme = queries.hybrid_search("deploy")
            with access.use_access(context(9, "beta")):
                beta = queries.hybrid_search("deploy")
        self.assertEqual(acme[0]["title"], "Acme only")
        self.assertEqual(beta[0]["title"], "Beta only")
        self.assertEqual(embed.call_count, 2)
        self.assertEqual({(key[0], key[-1]) for key in queries._rank_cache},
                         {(7, "deploy"), (9, "deploy")})

    def test_slack_principal_only_retrieves_its_channel_and_public_docs(self):
        rows = [
            {"id": 1, "source": "slack", "title": "Channel A", "snippet": "deploy", "body": "",
             "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [],
             "boost": 1, "acl_visibility": "restricted", "acl_principals": ["channel:C-A"]},
            {"id": 2, "source": "slack", "title": "Channel B", "snippet": "deploy", "body": "",
             "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [],
             "boost": 1, "acl_visibility": "restricted", "acl_principals": ["channel:C-B"]},
            {"id": 3, "source": "website", "title": "Public", "snippet": "deploy", "body": "",
             "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [],
             "boost": 1, "acl_visibility": "public", "acl_principals": []},
        ]
        slack_access = access.external_access(
            7, "acme", "Acme", "slack", "install-1", principals=frozenset({"channel:C-A"}))
        with access.use_access(slack_access), patch.object(queries.llm, "embed", return_value=None), \
             patch.object(queries, "q", return_value=rows):
            result = queries.hybrid_search("deploy")
        self.assertEqual({row["title"] for row in result}, {"Channel A", "Public"})

    def test_vector_artifact_paths_are_project_partitioned(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                "os.environ", {"MARI_VECTOR_URI": directory}):
            one = retrieval.index_for(7)
            two = retrieval.index_for(9)
        self.assertEqual(one.path, Path(directory) / "projects" / "7")
        self.assertEqual(two.path, Path(directory) / "projects" / "9")
        self.assertNotEqual(one.path, two.path)

    def test_chat_history_is_scoped_to_project_and_owner(self):
        class Info:
            context = {"user": {"id": 42}}

        rows = [[{"id": 8, "title": "Private"}], [{
            "id": 9, "role": "user", "content": "secret", "sources": [],
        }]]
        with access.use_access(context(7, "acme")), patch.object(
                queries, "q", side_effect=rows) as query:
            result = queries.Query().chat_sessions(Info())
        self.assertEqual(result[0].messages[0].content, "secret")
        self.assertEqual(query.call_args_list[0].args[1], (7, 42))
        self.assertEqual(query.call_args_list[1].args[1], (7, 8))

    def test_mcp_token_bootstraps_its_project_context(self):
        class Request:
            async def body(self):
                return b'{"jsonrpc":"2.0","id":1,"method":"ping"}'

        server = {"id": 3, "name": "Acme KB", "config": {"capabilities": ["search"]},
                  "project_id": 7, "project_slug": "acme", "project_name": "Acme"}
        seen = []

        def dispatch(_server, _message):
            seen.append(access.require_current_access())
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

        with patch.object(mcp, "q1", return_value=server), patch.object(mcp, "dispatch", side_effect=dispatch):
            asyncio.run(mcp.mcp_endpoint("acme-kb", Request(), "Bearer secret"))
        self.assertEqual(seen[0].project_id, 7)
        self.assertEqual(seen[0].principal_type, "mcp")
        self.assertIsNone(access.current_access())

    def test_slack_team_routes_to_one_project_before_work_starts(self):
        payload = {"type": "event_callback", "team_id": "T-ACME",
                   "event": {"type": "app_mention", "text": "<@B> deploy", "channel": "C", "ts": "1"}}
        raw = json.dumps(payload).encode()
        now = str(int(time.time()))
        signature = "v0=" + hmac.new(b"acme-secret", f"v0:{now}:".encode() + raw,
                                      hashlib.sha256).hexdigest()

        class Request:
            headers = {"X-Slack-Request-Timestamp": now, "X-Slack-Signature": signature}

            async def body(self):
                return raw

        installation = {"id": 5, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
                        "config": {"signing_secret": "acme-secret", "bot_token": "xoxb"}}
        submitted = []

        class Executor:
            def submit(self, *args):
                submitted.append(args)
                return True

        class Ledger:
            def claim(self, *_args): return True
            def release(self, *_args): pass

        with patch.object(bots, "q1", return_value=installation) as lookup, \
             patch.object(bots, "_SLACK_EXECUTOR", Executor()), \
             patch.object(bots, "_SLACK_EVENTS", Ledger()):
            result = asyncio.run(bots.slack_webhook(Request()))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(lookup.call_args.args[1], ("T-ACME",))
        routed = submitted[0][3]
        self.assertEqual(routed.project_id, 7)
        self.assertEqual(routed.principal_type, "slack")


if __name__ == "__main__":
    unittest.main()

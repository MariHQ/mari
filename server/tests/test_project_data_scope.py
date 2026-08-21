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

from mari_server.identity import access
from mari_server.destinations import slack as bots
from mari_server.persistence.postgres import database as db
from mari_server.destinations import mcp
from mari_server.product import queries
from mari_server.search import service as search_service
from mari_server.persistence.postgres import search as search_repository
from mari_server.providers import vectors as retrieval
from mari_server.knowledge import graphql as mutations_knowledge


def context(project_id: int, slug: str) -> access.AccessContext:
    return access.AccessContext(
        user_id=1, project_id=project_id, project_slug=slug,
        project_name=slug.title(), role="admin", capabilities=access.CAPABILITIES)


class ProjectDataScopeTests(unittest.TestCase):
    def tearDown(self):
        access.set_access(None)
        search_service._vec_cache.clear()
        search_service._rank_cache.clear()
        retrieval._INDEXES.clear()

    def test_project_db_helper_prepends_only_active_project(self):
        with access.use_access(context(7, "acme")), patch.object(db, "q", return_value=[]) as query:
            db.pq("SELECT id FROM documents WHERE project_id = %s AND title = %s", ("Runbook",))
        self.assertEqual(query.call_args.args[1], (7, "Runbook"))
        with self.assertRaises(RuntimeError):
            db.pq("SELECT 1 WHERE %s = %s", (1,))

    def test_natural_language_search_uses_meaningful_literal_terms(self):
        self.assertEqual(
            search_repository.keyword_patterns("How long are customer records retained?"),
            ["%long%", "%customer%", "%records%", "%retained%"],
        )
        self.assertEqual(search_repository.keyword_patterns("100%_safe"), ["%100%", "%safe%"])

    def test_core_knowledge_queries_always_bind_active_project(self):
        seen = []
        def scoped_rows():
            seen.append(access.require_current_access().project_id)
            return []
        with access.use_access(context(7, "acme")), \
             patch.object(queries.knowledge_store, "facts", side_effect=scoped_rows), \
             patch.object(queries.knowledge_store, "tasks", side_effect=scoped_rows), \
             patch.object(queries.knowledge_store, "glossary_terms", side_effect=scoped_rows), \
             patch.object(queries.knowledge_store, "approved_answers", side_effect=scoped_rows), \
             patch.object(queries.knowledge_store, "decisions_with_supersession", side_effect=scoped_rows):
            service = queries.Query()
            service.facts()
            service.tasks()
            service.glossary()
            service.approved_answers()
            service.decisions()
        self.assertEqual(seen, [7, 7, 7, 7, 7])

    def test_graphql_bot_status_passes_the_authorized_project_explicitly(self):
        project = context(7, "acme")
        with access.use_access(project), patch.object(bots, "bots_status", return_value={}) as status:
            self.assertEqual(queries.Query().bots_status(), {})
        status.assert_called_once_with(project)

    def test_foreign_knowledge_ids_fail_closed_before_write(self):
        with access.use_access(context(7, "acme")), \
             patch.object(mutations_knowledge.knowledge_store, "set_task_done", return_value=None) as task, \
             patch.object(mutations_knowledge.knowledge_store, "ratify_decision", return_value=None) as decision, \
             patch.object(mutations_knowledge.knowledge_store, "set_answer_channels", return_value=None) as answer:
            self.assertFalse(mutations_knowledge.MutKnowledge().set_task_done(99, True))
            self.assertFalse(mutations_knowledge.MutKnowledge().ratify_decision(99))
            self.assertFalse(mutations_knowledge.MutKnowledge().set_answer_channels(99, ["slack"]))
        task.assert_called_once_with(99, True)
        decision.assert_called_once_with(99)
        answer.assert_called_once_with(99, ["slack"])

    def test_embedding_and_rank_caches_do_not_cross_projects(self):
        rows = {
            7: [{"id": 1, "source": "docs", "title": "Acme only", "snippet": "deploy", "body": "",
                 "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [], "boost": 1}],
            9: [{"id": 2, "source": "docs", "title": "Beta only", "snippet": "deploy", "body": "",
                 "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [], "boost": 1}],
        }

        def scoped_rows(_project_id, _patterns, _limit):
            return [dict(row) for row in rows[access.require_current_access().project_id]]

        with patch.object(search_service.llm, "embed", return_value=None) as embed, \
             patch.object(search_service.search_store, "keyword_candidates", side_effect=scoped_rows):
            with access.use_access(context(7, "acme")):
                acme = search_service.hybrid_search("deploy")
            with access.use_access(context(9, "beta")):
                beta = search_service.hybrid_search("deploy")
        self.assertEqual(acme[0]["title"], "Acme only")
        self.assertEqual(beta[0]["title"], "Beta only")
        self.assertEqual(embed.call_count, 2)
        self.assertEqual({(key[0], key[-1]) for key in search_service._rank_cache},
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
            {"id": 4, "source": "confluence", "title": "Project knowledge", "snippet": "deploy", "body": "",
             "author": "", "author_initials": "", "updated_src": None, "kind": "page", "tags": [],
             "boost": 1, "acl_visibility": "connector_scope", "acl_principals": []},
        ]
        slack_access = access.external_access(
            7, "acme", "Acme", "slack", "install-1", principals=frozenset({"channel:C-A"}))
        with access.use_access(slack_access), patch.object(search_service.llm, "embed", return_value=None), \
             patch.object(search_service.search_store, "keyword_candidates", return_value=rows):
            result = search_service.hybrid_search("deploy")
        self.assertEqual(
            {row["title"] for row in result},
            {"Channel A", "Public", "Project knowledge"},
        )

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

        rows = [({"id": 8, "title": "Private"}, [{
            "id": 9, "role": "user", "content": "secret", "sources": [],
        }])]
        seen = []
        def sessions(user_id):
            seen.append((access.require_current_access().project_id, user_id))
            return rows
        with access.use_access(context(7, "acme")), patch.object(
                queries.chat_store, "sessions_for_owner", side_effect=sessions):
            result = queries.Query().chat_sessions(Info())
        self.assertEqual(result[0].messages[0].content, "secret")
        self.assertEqual(seen, [(7, 42)])

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

        with patch.object(mcp.mcp_repository, "authenticate", return_value=server), \
             patch.object(mcp, "dispatch", side_effect=dispatch):
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
        accepted = []
        class Inbox:
            def enqueue(self, provider, project_id, delivery_id, payload, **kwargs):
                accepted.append((provider, project_id, delivery_id, payload, kwargs))
                return 1, True

        with patch.object(bots.bot_store, "installation_by_team", return_value=installation) as lookup, \
             patch.object(bots, "_EVENT_INBOX", Inbox()):
            result = asyncio.run(bots.slack_webhook(Request()))
        self.assertEqual(result, {"ok": True})
        lookup.assert_called_once_with("T-ACME")
        self.assertEqual(accepted[0][1], 7)
        self.assertEqual(accepted[0][3]["installation_id"], 5)


if __name__ == "__main__":
    unittest.main()

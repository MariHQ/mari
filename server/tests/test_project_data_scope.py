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
        self.assertEqual(set(queries._rank_cache), {(7, "deploy"), (9, "deploy")})

    def test_vector_artifact_paths_are_project_partitioned(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                "os.environ", {"MARI_VECTOR_URI": directory}):
            one = retrieval.index_for(7)
            two = retrieval.index_for(9)
        self.assertEqual(one.path, Path(directory) / "projects" / "7")
        self.assertEqual(two.path, Path(directory) / "projects" / "9")
        self.assertNotEqual(one.path, two.path)

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
        with patch.object(bots, "q1", return_value=installation) as lookup, \
             patch.object(bots.threading, "Thread") as thread:
            result = asyncio.run(bots.slack_webhook(Request()))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(lookup.call_args.args[1], ("T-ACME",))
        routed = thread.call_args.kwargs["args"][2]
        self.assertEqual(routed.project_id, 7)
        self.assertEqual(routed.principal_type, "slack")


if __name__ == "__main__":
    unittest.main()

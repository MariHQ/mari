from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bots
import github
import access
import mutations_admin


class GitHubPollingTests(unittest.TestCase):
    def test_connect_repo_uses_the_active_project_for_every_write(self) -> None:
        with patch.object(mutations_admin, "_require_admin", return_value={"name": "Admin"}), \
             patch.object(mutations_admin.access, "require_current_access",
                          return_value=SimpleNamespace(project_id=7)), \
             patch.object(mutations_admin.github, "token", return_value="configured"), \
             patch.object(mutations_admin.github, "push_token", return_value=None), \
             patch.object(mutations_admin.github, "pop_token"), \
             patch.object(mutations_admin.github, "default_branch", return_value="main"), \
             patch.object(mutations_admin, "q1", side_effect=[None, {"id": 12}]) as query, \
             patch.object(mutations_admin, "exec_") as execute, \
             patch.object(mutations_admin, "audit"), \
             patch.object(mutations_admin.flowengine, "ensure_sync_flow"), \
             patch.object(mutations_admin.ingest, "start_sync"):
            source_id = mutations_admin.MutAdmin().connect_github_repo(object(), "acme/docs")
        self.assertEqual(source_id, 12)
        self.assertEqual(query.call_args_list[0].args[1], (7, "acme/docs"))
        self.assertEqual(execute.call_args.args[1][0], 7)
        self.assertEqual(query.call_args_list[1].args[1], (7, "github:acme/docs"))

    def test_connect_repo_rejects_case_only_duplicate(self) -> None:
        with patch.object(mutations_admin, "_require_admin", return_value={"name": "Admin"}), \
             patch.object(mutations_admin.access, "require_current_access",
                          return_value=SimpleNamespace(project_id=7)), \
             patch.object(mutations_admin.github, "token", return_value="configured"), \
             patch.object(mutations_admin, "q1", return_value={"id": 1}) as query, \
             patch.object(mutations_admin.github, "default_branch") as branch:
            with self.assertRaisesRegex(ValueError, "already connected"):
                mutations_admin.MutAdmin().connect_github_repo(object(), "MariHQ/mari/")

        self.assertIn("lower(config->>'repo') = lower(%s)", query.call_args.args[0])
        self.assertEqual(query.call_args.args[1], (7, "MariHQ/mari"))
        branch.assert_not_called()

    def test_transient_requests_retry_but_auth_does_not(self) -> None:
        sleeps = []
        with patch.object(github, "_request_once",
                          side_effect=[github.GithubError("HTTP 503", 503), ({"ok": True}, {})]) as req, \
             patch.object(github, "_RETRY_SLEEP", side_effect=sleeps.append):
            self.assertEqual(github._request("/x")[0], {"ok": True})
        self.assertEqual(req.call_count, 2)
        self.assertEqual(sleeps, [1])

        with patch.object(github, "_request_once",
                          side_effect=github.GithubError("bad token", 401)) as req:
            with self.assertRaises(github.GithubError):
                github._request("/x")
        self.assertEqual(req.call_count, 1)

    def test_truncated_recursive_tree_is_walked_without_losing_paths(self) -> None:
        recursive = ({"truncated": True, "tree": []}, {})
        root = ({"tree": [{"path": "README.md", "type": "blob", "sha": "b1"},
                            {"path": "docs", "type": "tree", "sha": "t1"}]}, {})
        docs = ({"tree": [{"path": "guide.md", "type": "blob", "sha": "b2"}]}, {})
        with patch.object(github, "_request", side_effect=[recursive, root, docs]):
            tree = github.get_tree("acme/docs", "head")
        self.assertTrue(tree.complete)
        self.assertEqual({node["path"] for node in tree}, {"README.md", "docs/guide.md"})

    def test_tree_traversal_cap_is_explicitly_incomplete(self) -> None:
        recursive = ({"truncated": True, "tree": []}, {})
        root = ({"tree": [{"path": "docs", "type": "tree", "sha": "t1"}]}, {})
        with patch.object(github, "_request", side_effect=[recursive, root]):
            tree = github.get_tree("acme/docs", "head", request_cap=1)
        self.assertFalse(tree.complete)

    def test_paginate_stops_on_short_page_and_reports_safety_cap(self) -> None:
        with patch.object(github, "_request", side_effect=[([{"id": i} for i in range(100)], {}), ([{"id": 101}], {})]) as req:
            rows, truncated = github._paginate("/repos/acme/docs/issues", {"since": "cursor"}, max_pages=3)
        self.assertEqual(len(rows), 101)
        self.assertFalse(truncated)
        self.assertEqual(req.call_args_list[1].args[1]["page"], 2)

        with patch.object(github, "_request", return_value=([{}] * 100, {})):
            _, truncated = github._paginate("/x", {}, max_pages=2)
        self.assertTrue(truncated)

    def test_source_token_override_is_scoped_and_reset(self) -> None:
        with patch.object(github.config, "get", return_value="workspace-token"):
            state = github.push_token("source-token")
            self.assertEqual(github.token(), "source-token")
            github.pop_token(state)
            self.assertEqual(github.token(), "workspace-token")

    def test_webhook_signature_accepts_rotating_configured_secrets(self) -> None:
        raw = b'{"repository":{"full_name":"acme/docs"}}'
        sig = "sha256=" + hmac.new(b"new-secret", raw, hashlib.sha256).hexdigest()
        self.assertTrue(bots.verify_github_signature(raw, sig, ["old-secret", "new-secret"]))
        self.assertFalse(bots.verify_github_signature(raw + b" ", sig, ["new-secret"]))
        self.assertFalse(bots.verify_github_signature(raw, sig, []))


class SlackBotTests(unittest.TestCase):
    def test_signature_accepts_current_exact_body_and_rejects_tampering(self) -> None:
        raw = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"secret", f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
        self.assertTrue(bots.verify_slack_signature(raw, ts, sig, "secret"))
        self.assertFalse(bots.verify_slack_signature(raw + b" ", ts, sig, "secret"))
        self.assertFalse(bots.verify_slack_signature(raw, str(int(ts) - 301), sig, "secret"))

    def test_event_handler_strips_mention_answers_and_posts_in_thread(self) -> None:
        with patch.object(bots, "answer_question", return_value="Use the runbook [1].") as answer, \
             patch.object(bots, "slack_call", return_value={"ok": True}) as call, \
             patch.object(bots, "merge_setting") as merge:
            bots._handle_slack_event({"type": "app_mention", "text": "<@UBOT> deploy?", "channel": "C1", "ts": "1.2"}, "xoxb-token")
        answer.assert_called_once_with("deploy?")
        self.assertEqual(call.call_args.args[0], "chat.postMessage")
        self.assertEqual(call.call_args.args[2]["thread_ts"], "1.2")
        self.assertNotIn("xoxb-token", json.dumps(merge.call_args.args))

    def test_answer_pipeline_uses_ollama_and_cites_retrieved_docs(self) -> None:
        docs = [{"title": "Deploy", "source": "github", "body": "Run make deploy", "snippet": ""}]
        ctx = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)
        with access.use_access(ctx), patch.object(bots.llm, "embed", return_value=None), \
             patch.object(bots, "hybrid_search", return_value=docs), \
             patch.object(bots, "pq", return_value=[]), \
             patch.object(bots.llm, "generate", return_value="Follow the deploy runbook [1].") as generate:
            out = bots.answer_question("How do I deploy?")
        self.assertIn("Follow the deploy runbook", out)
        self.assertIn("Sources: [1] Deploy", out)
        self.assertIn("Run make deploy", generate.call_args.args[0])

    def test_url_verification_challenge_does_not_require_installed_secret(self) -> None:
        class Request:
            headers = {}
            async def body(self):
                return b'{"type":"url_verification","challenge":"abc123"}'
        self.assertEqual(asyncio.run(bots.slack_webhook(Request())), {"challenge": "abc123"})


if __name__ == "__main__":
    unittest.main()

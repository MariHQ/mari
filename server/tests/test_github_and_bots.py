from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch

from mari_components import AuthenticationFailure, TransientFailure
from mari_components.connectors import GitHubConfig, github_issues, github_repository, github_tree
from mari_components.http import HttpResponse
from mari_server.destinations import slack as bots
from mari_server.providers import github
from mari_server.identity import access
from mari_server.identity import graphql as mutations_admin


class GitHubPollingTests(unittest.TestCase):
    class Http:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.requests = []

        def __call__(self, request):
            self.requests.append(request)
            value = next(self.responses)
            if isinstance(value, HttpResponse):
                return value
            return HttpResponse(200, {}, json.dumps(value).encode())

    def test_connect_repo_uses_the_active_project_for_every_write(self) -> None:
        with patch.object(mutations_admin, "_require_admin", return_value={"name": "Admin"}), \
             patch.object(mutations_admin.github, "default_branch", return_value="main") as branch, \
             patch.object(mutations_admin.admin_store, "add_github_source", return_value=12) as add_source, \
             patch.object(mutations_admin, "audit"), \
             patch.object(mutations_admin.flowengine, "ensure_sync_flow"), \
             patch.object(mutations_admin.ingest, "start_sync"):
            source_id = mutations_admin.MutAdmin().connect_github_repo(
                object(), "acme/docs", token="explicit-token"
            )
        self.assertEqual(source_id, 12)
        branch.assert_called_once_with("explicit-token", "acme/docs")
        self.assertEqual(add_source.call_args.args[0], "acme/docs")
        stored_config = add_source.call_args.args[1]
        self.assertEqual(stored_config["provider_key"], "github")
        self.assertEqual(stored_config["token"], "explicit-token")
        self.assertIn("item_hashes", stored_config)
        self.assertNotIn("shas", stored_config)

    def test_connect_repo_rejects_case_only_duplicate(self) -> None:
        with patch.object(mutations_admin, "_require_admin", return_value={"name": "Admin"}), \
             patch.object(mutations_admin.github, "default_branch", return_value="main"), \
             patch.object(mutations_admin.admin_store, "add_github_source",
                          side_effect=ValueError("Repository MariHQ/mari is already connected")) as add_source:
            with self.assertRaisesRegex(ValueError, "already connected"):
                mutations_admin.MutAdmin().connect_github_repo(
                    object(), "MariHQ/mari/", token="explicit-token"
                )

        self.assertEqual(add_source.call_args.args[0], "MariHQ/mari")

    def test_transport_classifies_transient_and_auth_failures(self) -> None:
        for status, error in ((503, TransientFailure), (401, AuthenticationFailure)):
            with self.subTest(status=status), self.assertRaises(error):
                github_repository(
                    GitHubConfig("token", "acme/docs"),
                    http=self.Http([HttpResponse(status, {}, b"{}")]),
                )

    def test_truncated_recursive_tree_is_walked_without_losing_paths(self) -> None:
        http = self.Http([
            {"truncated": True, "tree": []},
            {"tree": [{"path": "README.md", "type": "blob", "sha": "b1"},
                      {"path": "docs", "type": "tree", "sha": "t1"}]},
            {"tree": [{"path": "guide.md", "type": "blob", "sha": "b2"}]},
        ])
        tree, complete = github_tree(GitHubConfig("token", "acme/docs"), "head", http=http)
        self.assertTrue(complete)
        self.assertEqual({node["path"] for node in tree}, {"README.md", "docs/guide.md"})

    def test_tree_traversal_cap_is_explicitly_incomplete(self) -> None:
        http = self.Http([
            {"truncated": True, "tree": []},
            {"tree": [{"path": "docs", "type": "tree", "sha": "t1"}]},
        ])
        _tree, complete = github_tree(
            GitHubConfig("token", "acme/docs"), "head", http=http, request_limit=1,
        )
        self.assertFalse(complete)

    def test_paginate_stops_on_short_page_and_reports_safety_cap(self) -> None:
        http = self.Http([[{"id": i} for i in range(100)], [{"id": 101}]])
        rows, complete = github_issues(
            GitHubConfig("token", "acme/docs"), "cursor", http=http, page_limit=3,
        )
        self.assertEqual(len(rows), 101)
        self.assertTrue(complete)
        self.assertIn("page=2", http.requests[1].url)

        capped = self.Http([[{}] * 100, [{}] * 100])
        _, complete = github_issues(
            GitHubConfig("token", "acme/docs"), http=capped, page_limit=2,
        )
        self.assertFalse(complete)

    def test_repository_token_is_an_explicit_component_input(self) -> None:
        http = self.Http([{"full_name": "acme/docs"}])
        github_repository(GitHubConfig("source-token", "acme/docs"), http=http)
        self.assertEqual(http.requests[0].headers["Authorization"], "Bearer source-token")

    def test_webhook_signature_accepts_rotating_configured_secrets(self) -> None:
        raw = b'{"repository":{"full_name":"acme/docs"}}'
        sig = "sha256=" + hmac.new(b"new-secret", raw, hashlib.sha256).hexdigest()
        self.assertTrue(bots.verify_github_signature(raw, sig, ["old-secret", "new-secret"]))
        self.assertFalse(bots.verify_github_signature(raw + b" ", sig, ["new-secret"]))
        self.assertFalse(bots.verify_github_signature(raw, sig, []))


class SlackBotTests(unittest.TestCase):
    def test_manifest_enables_two_way_app_home_messages(self) -> None:
        manifest = bots.slack_manifest()
        self.assertIn("messages_tab_enabled: true", manifest)
        self.assertIn("messages_tab_read_only_enabled: false", manifest)
        self.assertIn("message.im", manifest)

    def test_signature_accepts_current_exact_body_and_rejects_tampering(self) -> None:
        raw = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"secret", f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
        self.assertTrue(bots.verify_slack_signature(raw, ts, sig, "secret"))
        self.assertFalse(bots.verify_slack_signature(raw + b" ", ts, sig, "secret"))
        self.assertFalse(bots.verify_slack_signature(raw, str(int(ts) - 301), sig, "secret"))

    def test_event_handler_strips_mention_and_posts_in_channel(self) -> None:
        with patch.object(bots, "answer_question", return_value="Use the runbook [1].") as answer, \
             patch.object(bots, "slack_call", return_value={"ok": True}) as call, \
             patch.object(bots, "merge_setting") as merge:
            bots._handle_slack_event({"type": "app_mention", "text": "<@UBOT> deploy?", "channel": "C1", "ts": "1.2"}, "xoxb-token")
        answer.assert_called_once_with("deploy?")
        self.assertEqual(call.call_args.args[0], "chat.postMessage")
        self.assertIsNone(call.call_args.args[2]["thread_ts"])
        self.assertNotIn("xoxb-token", json.dumps(merge.call_args.args))

    def test_answer_pipeline_uses_ollama_and_cites_retrieved_docs(self) -> None:
        docs = [{"title": "Deploy", "source": "github", "body": "Run make deploy", "snippet": ""}]
        ctx = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)
        with access.use_access(ctx), patch.object(bots.llm, "embed", return_value=None), \
             patch.object(bots, "hybrid_search", return_value=docs), \
             patch.object(bots.bot_store, "verified_facts", return_value=[]), \
             patch.object(bots.llm, "generate_json", return_value={
                 "answer": "Follow the deploy runbook [1].", "confidence": .99,
                 "evidence": [{"document_id": "document:1", "quote": "Run make deploy"}],
             }) as generate:
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

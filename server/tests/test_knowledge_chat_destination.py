from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from mari_server.destinations import chat as chat_api, graphql as graphql_destinations
from mari_server.conversations import chat as conversation_chat
from mari_components.destinations import knowledge_chat


def info(project_id: int = 7):
    return SimpleNamespace(context={"access": SimpleNamespace(project_id=project_id), "user": {"role": "admin"}})


class KnowledgeChatDestinationTests(unittest.TestCase):
    def test_terse_entity_prompt_is_not_rewritten_for_retrieval_or_generation(self):
        project = SimpleNamespace(project_id=7, user_id=0)
        document = {"id": 4, "title": "Mari README", "source": "github",
                    "body": "Mari manages product knowledge.", "snippet": "Mari manages"}
        with patch.object(conversation_chat.chat_store, "create_session", return_value=9), \
             patch.object(conversation_chat.chat_store, "add_message"), \
             patch.object(conversation_chat, "select_workflow", return_value=None), \
             patch.object(conversation_chat.chat_store, "messages", return_value=[
                 {"role": "user", "content": "mari"},
             ]), patch.object(conversation_chat, "hybrid_search", return_value=[document]) as search:
            context = conversation_chat.ports(project, "test", frozenset({"search"})).prepare(
                None, "mari",
            )
        search.assert_called_once_with("mari", 8)
        self.assertIn("Question: mari", context.messages[-1]["content"])

    def test_create_validates_slug_and_calls_application_port(self):
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda project, name, slug, title, welcome, tools: 12,
            update=lambda *_args: False,
            deploy=lambda *_args: None,
            audit=lambda *_args: None,
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chat_repository, "ports", return_value=ports):
            mut = graphql_destinations.DestinationMutations()
            self.assertEqual(
                mut.create_knowledge_chat_destination(
                    info(), "Company KB", "company-kb", "Ask Acme", ["search"], "Welcome",
                ),
                12,
            )
            with self.assertRaisesRegex(ValueError, "URL slug"):
                mut.create_knowledge_chat_destination(
                    info(), "Company KB", "Bad Slug", "Ask Acme", ["search"], "",
                )

    def test_update_and_deploy_cannot_cross_projects(self):
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda *_args: 1,
            update=lambda *_args: False,
            deploy=lambda *_args: None,
            audit=lambda *_args: None,
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chat_repository, "ports", return_value=ports):
            mut = graphql_destinations.DestinationMutations()
            self.assertFalse(mut.update_knowledge_chat_destination(info(7), 99, "Name", "Title", "Welcome", ["search"]))
            with self.assertRaisesRegex(ValueError, "not found"):
                mut.deploy_knowledge_chat_destination(info(7), 99)

    def test_deploy_returns_real_destination_url_and_audits(self):
        audits = []
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda *_args: 1,
            update=lambda *_args: False,
            deploy=lambda project, destination: ("Company KB", "/knowledge-chat/acme/company-kb"),
            audit=lambda verb, target: audits.append((verb, target)),
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chat_repository, "ports", return_value=ports):
            result = graphql_destinations.DestinationMutations().deploy_knowledge_chat_destination(info(), 12)
        self.assertEqual(result, "/knowledge-chat/acme/company-kb")
        self.assertEqual(audits, [("deployed knowledge chat", "Company KB")])

    def test_live_config_is_public_but_only_resolves_live_destination(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome",
               "tools": ["search"]}
        with patch.object(chat_api, "live_destination", return_value=row):
            self.assertEqual(chat_api.destination("acme", "company-kb")["title"], "Ask Acme")
        with patch.object(chat_api, "live_destination", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                chat_api.destination("acme", "company-kb")
            self.assertEqual(caught.exception.status_code, 404)

    def test_public_chat_uses_destination_scoped_read_access(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome",
               "tools": ["search"]}
        sentinel = object()
        with patch.object(chat_api, "live_destination", return_value=row), \
             patch.object(chat_api, "_sse", return_value=sentinel) as answer:
            result = chat_api.public_chat("acme", "company-kb", chat_api.ChatIn(message="policy"))
        self.assertIs(result, sentinel)
        destination_access = answer.call_args.args[0]
        self.assertEqual(destination_access.project_id, 7)
        self.assertEqual(destination_access.principal_type, "knowledge_chat")
        self.assertEqual(destination_access.capabilities, frozenset({"knowledge.read"}))
        self.assertEqual(answer.call_args.args[2], "knowledge_chat:2")
        self.assertEqual(answer.call_args.args[3], frozenset({"search"}))


if __name__ == "__main__":
    unittest.main()

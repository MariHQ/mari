from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from mari_server.destinations import chat as chat_api, graphql_destinations
from mari_components.destinations import knowledge_chat


def info(project_id: int = 7):
    return SimpleNamespace(context={"access": SimpleNamespace(project_id=project_id), "user": {"role": "admin"}})


class KnowledgeChatDestinationTests(unittest.TestCase):
    def test_create_validates_slug_and_calls_application_port(self):
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda project, name, slug, title, welcome: 12,
            update=lambda *_args: False,
            deploy=lambda *_args: None,
            audit=lambda *_args: None,
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chats as knowledge_chat_repository, "ports", return_value=ports):
            mut = graphql_destinations.DestinationMutations()
            self.assertEqual(
                mut.create_knowledge_chat_destination(
                    info(), "Company KB", "company-kb", "Ask Acme", "Welcome",
                ),
                12,
            )
            with self.assertRaisesRegex(ValueError, "URL slug"):
                mut.create_knowledge_chat_destination(
                    info(), "Company KB", "Bad Slug", "Ask Acme", "",
                )

    def test_update_and_deploy_cannot_cross_projects(self):
        ports = knowledge_chat.KnowledgeChatPorts(
            create=lambda *_args: 1,
            update=lambda *_args: False,
            deploy=lambda *_args: None,
            audit=lambda *_args: None,
        )
        with patch.object(graphql_destinations, "_require_admin"), \
             patch.object(graphql_destinations.knowledge_chats as knowledge_chat_repository, "ports", return_value=ports):
            mut = graphql_destinations.DestinationMutations()
            self.assertFalse(mut.update_knowledge_chat_destination(info(7), 99, "Name", "Title", "Welcome"))
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
             patch.object(graphql_destinations.knowledge_chats as knowledge_chat_repository, "ports", return_value=ports):
            result = graphql_destinations.DestinationMutations().deploy_knowledge_chat_destination(info(), 12)
        self.assertEqual(result, "/knowledge-chat/acme/company-kb")
        self.assertEqual(audits, [("deployed knowledge chat", "Company KB")])

    def test_live_config_is_public_but_only_resolves_live_destination(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome"}
        with patch.object(chat_api, "live_destination", return_value=row):
            self.assertEqual(chat_api.destination("acme", "company-kb")["title"], "Ask Acme")
        with patch.object(chat_api, "live_destination", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                chat_api.destination("acme", "company-kb")
            self.assertEqual(caught.exception.status_code, 404)

    def test_public_chat_uses_destination_scoped_read_access(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome"}
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


if __name__ == "__main__":
    unittest.main()

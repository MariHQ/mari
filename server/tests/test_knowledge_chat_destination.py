from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app
import mutations_publish


def info(project_id: int = 7):
    return SimpleNamespace(context={"access": SimpleNamespace(project_id=project_id), "user": {"role": "admin"}})


class KnowledgeChatDestinationTests(unittest.TestCase):
    def test_create_validates_slug_and_scopes_insert(self):
        mut = mutations_publish.MutPublish()
        with patch.object(mutations_publish, "_require_admin"), patch.object(mutations_publish, "q1") as q1, \
             patch.object(mutations_publish, "audit"):
            q1.side_effect = [None, {"id": 12}]
            self.assertEqual(mut.create_knowledge_chat_destination(info(), "Company KB", "company-kb", "Ask Acme", "Welcome"), 12)
            insert = q1.call_args_list[1]
            self.assertIn("INSERT INTO knowledge_chat_destinations", insert.args[0])
            self.assertEqual(insert.args[1][0], 7)
        with patch.object(mutations_publish, "_require_admin"):
            with self.assertRaisesRegex(ValueError, "URL slug"):
                mut.create_knowledge_chat_destination(info(), "Company KB", "Bad Slug", "Ask Acme", "")

    def test_update_and_deploy_cannot_cross_projects(self):
        mut = mutations_publish.MutPublish()
        with patch.object(mutations_publish, "_require_admin"), patch.object(mutations_publish, "q1", return_value=None), \
             patch.object(mutations_publish, "exec_") as execute:
            self.assertFalse(mut.update_knowledge_chat_destination(info(7), 99, "Name", "Title", "Welcome"))
            execute.assert_not_called()
            with self.assertRaisesRegex(ValueError, "not found"):
                mut.deploy_knowledge_chat_destination(info(7), 99)

    def test_deploy_returns_real_destination_url_and_marks_live(self):
        mut = mutations_publish.MutPublish()
        row = {"name": "Company KB", "slug": "company-kb", "project_slug": "acme"}
        with patch.object(mutations_publish, "_require_admin"), patch.object(mutations_publish, "q1", return_value=row), \
             patch.object(mutations_publish, "exec_") as execute, patch.object(mutations_publish, "audit"):
            self.assertEqual(mut.deploy_knowledge_chat_destination(info(), 12), "/knowledge-chat/acme/company-kb")
            self.assertIn("status = 'live'", execute.call_args.args[0])
            self.assertEqual(execute.call_args.args[1], (7, 12))

    def test_live_config_is_public_but_only_resolves_live_destination(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome"}
        with patch.object(app, "q1", return_value=row) as q1:
            self.assertEqual(app.knowledge_chat_destination("acme", "company-kb")["title"], "Ask Acme")
            self.assertEqual(q1.call_args.args[1], ("acme", "company-kb"))
            self.assertIn("status = 'live'", q1.call_args.args[0])
        with patch.object(app, "q1", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                app.knowledge_chat_destination("acme", "company-kb")
            self.assertEqual(caught.exception.status_code, 404)

    def test_public_chat_uses_destination_scoped_read_access(self):
        row = {"id": 2, "project_id": 7, "project_slug": "acme", "project_name": "Acme",
               "name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome"}
        sentinel = object()
        with patch.object(app, "q1", return_value=row), \
             patch.object(app, "_chat_for_access", return_value=sentinel) as answer:
            result = app.public_knowledge_chat("acme", "company-kb", app.ChatIn(message="policy"))
        self.assertIs(result, sentinel)
        access = answer.call_args.args[1]
        self.assertEqual(access.project_id, 7)
        self.assertEqual(access.principal_type, "knowledge_chat")
        self.assertEqual(access.capabilities, frozenset({"knowledge.read"}))
        self.assertEqual(answer.call_args.args[2], "knowledge_chat:2")


if __name__ == "__main__":
    unittest.main()

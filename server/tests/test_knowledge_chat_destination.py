from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

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

    def test_live_config_requires_session_membership_and_live_row(self):
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        project = SimpleNamespace(project_id=7)
        row = {"name": "Company KB", "slug": "company-kb", "title": "Ask Acme", "welcome": "Welcome"}
        with patch.object(app.auth_module, "current_user", return_value={"id": 3}), \
             patch.object(app.access_module, "resolve_access", return_value=(project, [])), \
             patch.object(app, "q1", return_value=row) as q1:
            self.assertEqual(app.knowledge_chat_destination("acme", "company-kb", request)["title"], "Ask Acme")
            self.assertEqual(q1.call_args.args[1], (7, "company-kb"))
            self.assertIn("status = 'live'", q1.call_args.args[0])

        with patch.object(app.auth_module, "current_user", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                app.knowledge_chat_destination("acme", "company-kb", request)
            self.assertEqual(caught.exception.status_code, 401)

        with patch.object(app.auth_module, "current_user", return_value={"id": 3}), \
             patch.object(app.access_module, "resolve_access", return_value=(project, [])), patch.object(app, "q1", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                app.knowledge_chat_destination("acme", "company-kb", request)
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

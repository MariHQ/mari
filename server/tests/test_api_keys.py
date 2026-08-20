from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

import access
import app
import mutations_admin


def context() -> access.AccessContext:
    return access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)


class ApiKeyTests(unittest.TestCase):
    def tearDown(self) -> None:
        access.set_access(None)

    def test_creation_stores_hash_only_and_rejects_duplicate_name(self) -> None:
        class Info:
            context = {"user": {"id": 1, "name": "Admin", "role": "admin"}}

        with access.use_access(context()), patch.object(mutations_admin, "q1", return_value=None), \
             patch.object(mutations_admin, "exec_") as execute, \
             patch.object(mutations_admin, "audit"):
            token = mutations_admin.MutAdmin().create_api_key(Info(), "Search", "search")
        stored = execute.call_args.args[1]
        self.assertEqual(stored[0:3], (7, "Search", token[:12] + "…"))
        self.assertEqual(stored[3], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, stored)

        with access.use_access(context()), patch.object(mutations_admin, "q1", return_value={"?column?": 1}):
            with self.assertRaisesRegex(ValueError, "already exists"):
                mutations_admin.MutAdmin().create_api_key(Info(), "Search", "search")

    def test_search_endpoint_bootstraps_key_project_and_updates_usage(self) -> None:
        row = {"id": 5, "project_id": 7, "scopes": "search", "slug": "acme", "project_name": "Acme"}
        seen = []

        def search(_query, _limit):
            seen.append(access.require_current_access())
            return [{"id": 2, "title": "Runbook", "source": "docs", "snippet": "deploy", "score": .9}]

        with patch.object(app, "q1", return_value=row), patch.object(app, "hybrid_search", side_effect=search), \
             patch.object(app, "exec_") as execute:
            result = app.api_search(app.ApiSearchIn(query="deploy"), "Bearer secret")
        self.assertEqual(result["results"][0]["title"], "Runbook")
        self.assertEqual(seen[0].project_id, 7)
        self.assertEqual(seen[0].principal_type, "api_key")
        self.assertIsNone(access.current_access())
        self.assertEqual(execute.call_args.args[1], (5,))


if __name__ == "__main__":
    unittest.main()

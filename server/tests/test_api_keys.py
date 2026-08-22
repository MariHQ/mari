from __future__ import annotations

import hashlib
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from mari_server.identity import access
from mari_server.search import routes as search_routes
from mari_server.identity import graphql as mutations_admin
from mari_server.persistence.postgres import admin as admin_store


def context() -> access.AccessContext:
    return access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)


class ApiKeyTests(unittest.TestCase):
    def tearDown(self) -> None:
        access.set_access(None)

    def test_creation_stores_hash_only_and_rejects_duplicate_name(self) -> None:
        class Info:
            context = {"user": {"id": 1, "name": "Admin", "role": "admin"}}

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self, duplicate=False):
                self.duplicate = duplicate
                self.calls = []

            def transaction(self):
                return nullcontext()

            def execute(self, sql, args):
                self.calls.append((sql, args))
                return Result({"exists": 1} if self.duplicate and "SELECT 1" in sql else None)

        connection = Connection()
        with access.use_access(context()), \
             patch.object(admin_store.db, "connect", return_value=nullcontext(connection)), \
             patch.object(mutations_admin, "audit"):
            token = mutations_admin.MutAdmin().create_api_key(Info(), "Search", "search")
        stored = next(args for sql, args in connection.calls if "INSERT INTO api_keys" in sql)
        self.assertEqual(stored[0:3], (7, "Search", token[:12] + "…"))
        self.assertEqual(stored[3], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, stored)

        duplicate = Connection(duplicate=True)
        with access.use_access(context()), \
             patch.object(admin_store.db, "connect", return_value=nullcontext(duplicate)):
            with self.assertRaisesRegex(ValueError, "already exists"):
                mutations_admin.MutAdmin().create_api_key(Info(), "Search", "search")

    def test_search_endpoint_bootstraps_key_project_and_updates_usage(self) -> None:
        row = {"id": 5, "project_id": 7, "scopes": "search", "slug": "acme", "project_name": "Acme"}
        seen = []

        def search(query, limit):
            seen.append(access.require_current_access())
            self.assertEqual((query, limit), ("deploy", 10))
            return [{"id": 2, "title": "Runbook", "snippet": "deploy",
                     "source": "docs", "score": .9}]

        with patch.object(search_routes.identity, "authenticate_api_key", return_value=row), \
             patch.object(search_routes.identity, "touch_api_key") as touch, \
             patch.object(search_routes, "hybrid_search", side_effect=search), \
             patch.object(search_routes, "hybrid_count", return_value=1):
            result = search_routes.api_search(search_routes.ApiSearchIn(query="deploy"), "Bearer secret")
        self.assertEqual(result.results[0].title, "Runbook")
        self.assertEqual(result.total, 1)
        self.assertAlmostEqual(result.results[0].score, 0.9 / 5.0)
        self.assertEqual(seen[0].project_id, 7)
        self.assertEqual(seen[0].principal_type, "api_key")
        self.assertIsNone(access.current_access())
        touch.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()

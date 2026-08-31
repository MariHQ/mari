from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from mari_server.identity import access
from mari_server.persistence.postgres import workflows


class WorkflowPersistenceTests(unittest.TestCase):
    def test_latest_visible_run_does_not_fall_back_after_dismissal(self) -> None:
        context = access.AccessContext(
            user_id=2, project_id=7, project_slug="acme", project_name="Acme",
            role="owner", capabilities=access.CAPABILITIES, principal_id="2",
        )
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        manager = Mock()
        manager.__enter__ = Mock(return_value=connection)
        manager.__exit__ = Mock(return_value=False)

        with access.use_access(context), patch.object(workflows.db, "connect", return_value=manager):
            self.assertIsNone(workflows.latest_visible_run(10))

        sql, parameters = connection.execute.call_args.args
        self.assertIn("WITH latest AS", sql)
        self.assertIn("ORDER BY number DESC LIMIT 1", sql)
        self.assertEqual(parameters, (7, 10, 2))


if __name__ == "__main__":
    unittest.main()

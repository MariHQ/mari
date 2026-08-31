from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch

from mari_server.identity import access
from mari_server.persistence.postgres import workflows


class WorkflowPersistenceTests(unittest.TestCase):
    def test_scheduler_never_runs_legacy_unscoped_workflows(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        manager = MagicMock()
        manager.__enter__.return_value = connection
        with patch.object(workflows.db, "connect", return_value=manager):
            self.assertEqual(workflows.scheduled_workflows(), [])
        sql = connection.execute.call_args.args[0]
        self.assertIn("project_id IS NOT NULL", sql)

    def test_setting_schedule_keeps_trigger_node_label_in_sync(self) -> None:
        context = access.AccessContext(
            user_id=2, project_id=7, project_slug="acme", project_name="Acme",
            role="owner", capabilities=access.CAPABILITIES, principal_id="2",
        )
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "nodes": [{"kind": "trigger", "label": "Manual", "config": {"label": "Started manually"}},
                      {"kind": "scan_facts", "config": {"max_llm_calls": 20}}],
        }
        manager = MagicMock()
        manager.__enter__.return_value = connection

        with access.use_access(context), patch.object(workflows.db, "connect", return_value=manager):
            self.assertTrue(workflows.set_trigger(10, {"on": "schedule", "every_minutes": 360}))

        update_sql, parameters = connection.execute.call_args.args
        self.assertIn("SET trigger = %s, nodes = %s", update_sql)
        self.assertIn('"label": "Every 6 hours"', parameters[1])
        self.assertIn('"max_llm_calls": 20', parameters[1])

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

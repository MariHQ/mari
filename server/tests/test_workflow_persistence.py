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

    def test_orphan_sync_workflows_are_archived_without_deleting_history(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{"id": 4}, {"id": 49}]
        manager = MagicMock()
        manager.__enter__.return_value = connection
        with patch.object(workflows.db, "connect", return_value=manager):
            self.assertEqual(workflows.quarantine_orphan_sync_workflows(), 2)
        sql = connection.execute.call_args.args[0]
        self.assertIn("SET status = 'archived'", sql)
        self.assertIn("w.project_id IS NULL", sql)
        self.assertNotIn("DELETE", sql)

    def test_scheduler_dueness_reads_the_newest_run_by_number(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        manager = MagicMock()
        manager.__enter__.return_value = connection
        with patch.object(workflows.db, "connect", return_value=manager):
            workflows.latest_run(10, 60)
        sql = connection.execute.call_args.args[0]
        # the same ordering list_workflows derives last-run from
        self.assertIn("ORDER BY number DESC", sql)
        self.assertNotIn("ORDER BY id DESC", sql)

    def test_run_creation_refuses_a_concurrent_running_run(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [
            {"name": "Fact extraction"},  # the workflow row, locked FOR UPDATE
            {"one": 1},                   # a run with status = 'running'
        ]
        with patch.object(workflows, "transaction", side_effect=lambda fn: fn(connection)):
            with self.assertRaisesRegex(ValueError, "already has a run in progress"):
                workflows._create_run(7, 10, False)
        lock_sql = connection.execute.call_args_list[0].args[0]
        self.assertIn("FOR UPDATE", lock_sql)
        probe_sql = connection.execute.call_args_list[1].args[0]
        self.assertIn("status = 'running'", probe_sql)
        self.assertFalse(any("INSERT INTO workflow_runs" in call.args[0]
                             for call in connection.execute.call_args_list))

    def test_delete_rechecks_for_a_running_run_under_the_row_lock(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [
            {"name": "Sync repo"},  # the workflow row, locked FOR UPDATE
            {"one": 1},             # a run that started after the GraphQL guard read
        ]
        with patch.object(workflows, "transaction", side_effect=lambda fn: fn(connection)):
            with self.assertRaisesRegex(ValueError, "still running"):
                workflows._delete(7, 10)
        self.assertFalse(any(call.args[0].lstrip().startswith("DELETE")
                             for call in connection.execute.call_args_list))

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

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mari_server.automations import graphql
from mari_server.identity import access


class WorkflowGraphqlTests(unittest.TestCase):
    def test_remove_scheduled_task_is_narrower_than_workflow_delete(self) -> None:
        context = access.external_access(7, "acme", "Acme", "test", "admin")
        row = {
            "id": 12, "status": "paused", "last_run_status": "passed",
            "trigger": {"on": "schedule", "every_minutes": 60},
            "nodes": [{"kind": "refresh_digest"}],
        }
        ports = Mock()
        with access.use_access(context), \
             patch.object(graphql.workflow_repository, "list_workflows", return_value=[row]), \
             patch.object(graphql.workflow_repository, "ports", return_value=ports), \
             patch.object(graphql.workflows, "delete", return_value=True) as delete:
            self.assertTrue(graphql.WorkflowMutations.remove_scheduled_task(None, 12))
        delete.assert_called_once_with(7, 12, ports=ports)

    def test_running_scheduled_task_cannot_be_removed(self) -> None:
        context = access.external_access(7, "acme", "Acme", "test", "admin")
        row = {
            "id": 12, "status": "active", "last_run_status": "running",
            "trigger": {"on": "schedule", "every_minutes": 60}, "nodes": [],
        }
        with access.use_access(context), \
             patch.object(graphql.workflow_repository, "list_workflows", return_value=[row]):
            with self.assertRaisesRegex(ValueError, "still running"):
                graphql.WorkflowMutations.remove_scheduled_task(None, 12)

    def test_continue_run_does_not_require_a_graphql_root_instance(self) -> None:
        context = access.external_access(7, "acme", "Acme", "test", "reviewer")
        info = SimpleNamespace(context={"user": {"name": "Dana"}})
        ports = Mock()

        with access.use_access(context), \
             patch.object(graphql.workflow_repository, "ports", return_value=ports), \
             patch.object(graphql.workflows, "approve", return_value=True) as approve:
            result = graphql.WorkflowMutations.approve_run(None, info, 2501)

        self.assertTrue(result)
        approve.assert_called_once_with(7, 2501, actor_name="Dana", ports=ports)

    def test_dismiss_run_is_a_durable_repository_command(self) -> None:
        with patch.object(graphql.workflow_repository, "dismiss_run", return_value=True) as dismiss:
            result = graphql.WorkflowMutations.dismiss_workflow_run(None, 2501)
        self.assertTrue(result)
        dismiss.assert_called_once_with(2501)


if __name__ == "__main__":
    unittest.main()

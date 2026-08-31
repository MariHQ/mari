from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mari_server.automations import graphql
from mari_server.identity import access


class WorkflowGraphqlTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

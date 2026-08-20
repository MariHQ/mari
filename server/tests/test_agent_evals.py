from __future__ import annotations

import unittest
from unittest.mock import patch

import access
import agent_evals
import agentchat


class AgentOutcomeEvals(unittest.TestCase):
    def setUp(self) -> None:
        self.project = access.AccessContext(
            user_id=1, project_id=7, project_slug="acme", project_name="Acme",
            role="admin", capabilities=access.CAPABILITIES,
        )

    def test_guided_setup_workflows_reach_the_product_and_explain_completion(self) -> None:
        for case in agent_evals.CASES:
            with self.subTest(case=case.name), access.use_access(self.project), \
                    patch.object(agentchat, "q", return_value=[]), \
                    patch.object(agentchat, "exec_"), \
                    patch.object(agentchat, "log_usage"), \
                    patch.object(agentchat.trajectory, "harvest"), \
                    patch.object(agentchat.llm, "generate") as generate:
                result = agent_evals.score(
                    case, agentchat.agent_events(11, case.prompt, self.project),
                )
            self.assertTrue(result["passed"], result)
            generate.assert_not_called()

    def test_every_guided_eval_targets_a_shipped_route(self) -> None:
        for case in agent_evals.CASES:
            with self.subTest(case=case.name):
                self.assertTrue(agentchat.valid_nav(case.expected_path), case.expected_path)

    def test_inventory_questions_return_grounded_product_state(self) -> None:
        rows = {
            "list_sources": [{"id": 1, "display_name": "Confluence", "provider": "confluence",
                              "kind": "connector", "status": "active", "health": "Healthy", "docs_count": 50}],
            "list_flows": [{"id": 2, "name": "Fact scan", "status": "active", "description": ""}],
            "list_tasks": [{"id": 3, "title": "Verify retention", "kind": "factcheck",
                            "kind_label": "Fact check", "done": False}],
            "list_answers": [{"id": 4, "question": "How long is retention?", "status": "approved", "served": 8}],
        }
        for case in agent_evals.TOOL_CASES:
            def query(sql, _args=(), *, tool=case.expected_tool):
                if "chat_messages" in sql:
                    return []
                return rows[tool]
            with self.subTest(case=case.name), access.use_access(self.project), \
                    patch.object(agentchat, "q", side_effect=query), \
                    patch.object(agentchat.review, "project_items", return_value=[
                        agentchat.review.ReviewRecord(
                            id="task:3", kind="factcheck", title="Verify retention", status="pending",
                        ),
                    ]), \
                    patch.object(agentchat, "exec_"), \
                    patch.object(agentchat, "log_usage"), \
                    patch.object(agentchat.trajectory, "harvest"), \
                    patch.object(agentchat.llm, "generate") as generate:
                result = agent_evals.score_tool(
                    case, agentchat.agent_events(14, case.prompt, self.project),
                )
            self.assertTrue(result["passed"], result)
            generate.assert_not_called()

    def test_knowledge_questions_still_use_the_model_tool_loop(self) -> None:
        replies = iter([
            '{"tool":"search","args":{"query":"retention policy"}}',
            '{"answer":"The retention runbook is the relevant source."}',
        ])
        with access.use_access(self.project), \
                patch.object(agentchat, "q", return_value=[]), \
                patch.object(agentchat, "hybrid_search", return_value=[{
                    "id": 4, "title": "Retention runbook", "snippet": "30 days",
                }]), \
                patch.object(agentchat, "exec_"), \
                patch.object(agentchat, "log_usage"), \
                patch.object(agentchat.trajectory, "harvest"), \
                patch.object(agentchat.llm, "generate", side_effect=lambda *_args, **_kwargs: next(replies)):
            events = agent_evals.parse_events(
                agentchat.agent_events(12, "Find our retention policy", self.project),
            )
        self.assertTrue(any(event == "tool_result" and data.get("name") == "search" and data.get("ok")
                            for event, data in events))
        self.assertIn("retention runbook", "".join(
            data.get("token", "") for event, data in events if event == "token"
        ).lower())

    def test_document_inspection_searches_before_reading_a_real_id(self) -> None:
        replies = iter([
            '{"tool":"search","args":{"query":"retention runbook"}}',
            '{"tool":"read_document","args":{"id":4}}',
            '{"answer":"The runbook says deleted customer data is retained for 30 days."}',
        ])
        document = {
            "id": 4, "title": "Retention runbook", "source": "confluence", "author": "Dana",
            "updated_src": None, "body": "Deleted customer data is retained for 30 days.", "snippet": "",
        }
        def query(sql, _args=()):
            return []
        with access.use_access(self.project), \
                patch.object(agentchat, "q", side_effect=query), \
                patch.object(agentchat, "hybrid_search", return_value=[{
                    "id": 4, "title": "Retention runbook", "snippet": "30 days",
                }]), \
                patch.object(agentchat, "_need_doc", return_value=document), \
                patch.object(agentchat, "exec_"), \
                patch.object(agentchat, "log_usage"), \
                patch.object(agentchat.trajectory, "harvest"), \
                patch.object(agentchat.llm, "generate", side_effect=lambda *_args, **_kwargs: next(replies)):
            events = agent_evals.parse_events(
                agentchat.agent_events(13, "Read the retention runbook", self.project),
            )
        tools = [data["name"] for event, data in events if event == "tool_result"]
        self.assertEqual(tools, ["search", "read_document"])
        self.assertIn("30 days", "".join(
            data.get("token", "") for event, data in events if event == "token"
        ))


if __name__ == "__main__":
    unittest.main()

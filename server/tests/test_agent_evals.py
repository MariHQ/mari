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

    def test_product_workflows_are_composed_from_tools_not_keyword_routes(self) -> None:
        self.assertFalse(hasattr(agentchat, "guided_workflow"))
        self.assertFalse(hasattr(agentchat, "direct_read"))
        for case in agent_evals.CASES:
            replies = iter([
                {"action": "tool", "tool": "list_product_surfaces", "arguments": {}},
                {"action": "tool", "tool": "navigate",
                 "arguments": {"path": case.expected_path}},
                {"action": "answer"},
            ])
            with self.subTest(case=case.name), access.use_access(self.project), \
                    patch.object(agentchat, "q", return_value=[]), \
                    patch.object(agentchat, "exec_"), \
                    patch.object(agentchat, "log_usage"), \
                    patch.object(agentchat.trajectory, "harvest"), \
                    patch.object(agentchat.llm, "generate_json",
                                 side_effect=lambda *_args, **_kwargs: next(replies)) as generate, \
                    patch.object(agentchat.llm, "chat_stream",
                                 return_value=iter([" ".join(case.required_terms)])):
                result = agent_evals.score(
                    case, agentchat.agent_events(11, case.prompt, self.project),
                )
            self.assertTrue(result["passed"], result)
            self.assertEqual(generate.call_count, 3)

    def test_every_product_eval_targets_a_shipped_route(self) -> None:
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
            replies = iter([
                {"action": "tool", "tool": case.expected_tool, "arguments": {}},
                {"action": "answer"},
            ])
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
                    patch.object(agentchat.llm, "generate_json",
                                 side_effect=lambda *_args, **_kwargs: next(replies)) as generate, \
                    patch.object(agentchat.llm, "chat_stream",
                                 return_value=iter([" ".join(case.answer_terms)])):
                result = agent_evals.score_tool(
                    case, agentchat.agent_events(14, case.prompt, self.project),
                )
            self.assertTrue(result["passed"], result)
            self.assertEqual(generate.call_count, 2)

    def test_workflow_refinement_uses_automation_and_trajectory_evidence(self) -> None:
        replies = iter([
            {"action": "tool", "tool": "list_flows", "arguments": {}},
            {"action": "tool", "tool": "inspect_flow", "arguments": {"id": 2}},
            {"action": "tool", "tool": "list_workflow_observations",
             "arguments": {"query": "fact scan"}},
            {"action": "tool", "tool": "inspect_workflow_observation",
             "arguments": {"id": 9}},
            {"action": "answer"},
        ])

        def query(sql, _args=()):
            if "chat_messages" in sql:
                return []
            if "FROM workflows" in sql:
                return [{"id": 2, "name": "Fact scan", "status": "active", "description": ""}]
            if "FROM workflow_runs" in sql:
                return [{"id": 4, "number": 3, "status": "failed", "progress": 50,
                         "stats": {}, "rows_data": [], "triggered_by": "document change"}]
            if "FROM trajectories" in sql:
                return [{"id": 9, "prompt": "refine fact scan", "status": "ready",
                         "layer2": "Ran fact scan", "category": "Automation",
                         "macro_intent": "Improve fact scan", "step_count": 2,
                         "failure_count": 1, "rework_count": 1, "started_at": None}]
            if "FROM trajectory_steps" in sql:
                return [{"ordinal": 0, "tool": "search", "action_family": "discover",
                         "summary": "found facts", "ok": True},
                        {"ordinal": 1, "tool": "run_flow", "action_family": "execute",
                         "summary": "validation failed", "ok": False}]
            return []

        def query_one(sql, args=()):
            if "FROM workflows" in sql:
                return {"id": 2, "name": "Fact scan", "description": "", "status": "active",
                        "nodes": [{"kind": "scan"}], "trigger": {"on": "document_changed"}}
            if "FROM trajectories" in sql:
                return {"id": 9, "prompt": "refine fact scan", "status": "ready",
                        "layer1": "Search then run", "layer2": "Ran fact scan",
                        "category": "Automation", "macro_intent": "Improve fact scan",
                        "phases": [], "step_count": 2, "failure_count": 1, "rework_count": 1}
            return None

        with access.use_access(self.project), patch.object(agentchat, "q", side_effect=query), \
                patch.object(agentchat, "q1", side_effect=query_one), \
                patch.object(agentchat, "exec_"), patch.object(agentchat, "log_usage"), \
                patch.object(agentchat.trajectory, "harvest"), \
                patch.object(agentchat.llm, "generate_json",
                             side_effect=lambda *_args, **_kwargs: next(replies)), \
                patch.object(agentchat.llm, "chat_stream", return_value=iter([
                    "The fact scan repeatedly fails after search; split validation into a separate step.",
                ])):
            events = agent_evals.parse_events(agentchat.agent_events(
                15, "Use our observations to refine the fact scan workflow", self.project,
            ))
        tools = [data["name"] for event, data in events if event == "tool_result"]
        self.assertEqual(tools, ["list_flows", "inspect_flow", "list_workflow_observations",
                                 "inspect_workflow_observation"])
        self.assertIn("validation", "".join(
            data.get("token", "") for event, data in events if event == "token"
        ).lower())

    def test_final_answer_reaches_sse_without_buffering(self) -> None:
        produced = []

        def answer_stream(*_args, **_kwargs):
            produced.append("first")
            yield "first "
            produced.append("second")
            yield "second"

        with access.use_access(self.project), patch.object(agentchat, "q", return_value=[]), \
                patch.object(agentchat, "exec_"), patch.object(agentchat, "log_usage"), \
                patch.object(agentchat.trajectory, "harvest"), \
                patch.object(agentchat.llm, "generate_json", return_value={"action": "answer"}), \
                patch.object(agentchat.llm, "chat_stream", side_effect=answer_stream):
            stream = agentchat.agent_events(16, "Explain this", self.project)
            self.assertIn("event: meta", next(stream))
            first = next(stream)
            self.assertIn('"token": "first "', first)
            self.assertEqual(produced, ["first"])
            second = next(stream)
            self.assertIn('"token": "second"', second)
            self.assertEqual(produced, ["first", "second"])
            tuple(stream)

    def test_knowledge_questions_still_use_the_model_tool_loop(self) -> None:
        replies = iter([
            {"action": "tool", "tool": "search", "arguments": {"query": "retention policy"}},
            {"action": "answer"},
        ])
        with access.use_access(self.project), \
                patch.object(agentchat, "q", return_value=[]), \
                patch.object(agentchat, "hybrid_search", return_value=[{
                    "id": 4, "title": "Retention runbook", "snippet": "30 days",
                }]), \
                patch.object(agentchat, "exec_"), \
                patch.object(agentchat, "log_usage"), \
                patch.object(agentchat.trajectory, "harvest"), \
                patch.object(agentchat.llm, "generate_json",
                             side_effect=lambda *_args, **_kwargs: next(replies)), \
                patch.object(agentchat.llm, "chat_stream",
                             return_value=iter(["The retention runbook is the relevant source."])):
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
            {"action": "tool", "tool": "search", "arguments": {"query": "retention runbook"}},
            {"action": "tool", "tool": "read_document", "arguments": {"id": 4}},
            {"action": "answer"},
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
                patch.object(agentchat.llm, "generate_json",
                             side_effect=lambda *_args, **_kwargs: next(replies)), \
                patch.object(agentchat.llm, "chat_stream", return_value=iter([
                    "The runbook says deleted customer data is retained for 30 days.",
                ])):
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

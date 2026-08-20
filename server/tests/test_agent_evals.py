from __future__ import annotations

import unittest
from types import SimpleNamespace

import agent_evals
from mari_server.api.agent import serialize_sse
from mari_server.application.agent import AgentPorts, stream_agent_turn
from mari_server.domain.navigation import valid_navigation
from mari_server.infrastructure.agent_tools import ToolDependencies, build_tool_bindings


def dependencies(*, query=lambda _sql, _args: (), query_one=lambda _sql, _args: None,
                 search=lambda _text, _limit: (), review_items=lambda: ()):
    return ToolDependencies(
        project_id=7, query=query, query_one=query_one, search=search,
        record_search=lambda _text: None, review_items=review_items,
        connector_definitions=lambda: (),
    )


def run(decisions, answer, deps=None):
    choices = iter(decisions)
    saved = []
    ports = AgentPorts(
        history=lambda _session: (),
        plan=lambda _prompt, _version: next(choices),
        answer=lambda _transcript: iter(answer),
        save_answer=lambda _session, text, trace: saved.append((text, trace)),
        observe_trajectory=lambda *_args: None,
        record_usage=lambda *_args: None,
    )
    outputs = stream_agent_turn(1, "request", build_tool_bindings(deps or dependencies()), ports)
    return serialize_sse(outputs), saved


class AgentOutcomeEvals(unittest.TestCase):
    def test_product_workflows_are_composed_from_tools(self) -> None:
        for case in agent_evals.CASES:
            events, _saved = run([
                {"action": "tool", "tool": "list_product_surfaces", "arguments": {}},
                {"action": "tool", "tool": "navigate", "arguments": {"path": case.expected_path}},
                {"action": "answer"},
            ], [" ".join(case.required_terms)])
            with self.subTest(case=case.name):
                self.assertTrue(agent_evals.score(case, events)["passed"])

    def test_every_product_eval_targets_a_shipped_route(self) -> None:
        for case in agent_evals.CASES:
            self.assertTrue(valid_navigation(case.expected_path), case.expected_path)

    def test_inventory_questions_return_grounded_state(self) -> None:
        rows = {
            "sources": [{"id": 1, "display_name": "Confluence", "provider": "confluence",
                         "kind": "connector", "status": "active", "health": "Healthy", "docs_count": 50}],
            "workflows": [{"id": 2, "name": "Fact scan", "status": "active", "description": ""}],
            "approved_answers": [{"id": 4, "question": "How long?", "status": "approved", "served": 8}],
        }

        def query(sql, _args):
            return next((value for key, value in rows.items() if key in sql), ())

        deps = dependencies(
            query=query,
            review_items=lambda: [SimpleNamespace(
                id="task:3", title="Verify retention", kind="factcheck", status="pending",
            )],
        )
        for case in agent_evals.TOOL_CASES:
            events, _saved = run([
                {"action": "tool", "tool": case.expected_tool, "arguments": {}},
                {"action": "answer"},
            ], [" ".join(case.answer_terms)], deps)
            with self.subTest(case=case.name):
                self.assertTrue(agent_evals.score_tool(case, events)["passed"])

    def test_workflow_refinement_uses_run_and_trajectory_evidence(self) -> None:
        def query(sql, _args):
            if "FROM workflows" in sql:
                return [{"id": 2, "name": "Fact scan", "status": "active", "description": ""}]
            if "FROM workflow_runs" in sql:
                return [{"id": 4, "number": 3, "status": "failed", "progress": 50,
                         "stats": {}, "rows_data": [], "triggered_by": "change"}]
            if "FROM trajectories" in sql:
                return [{"id": 9, "prompt": "refine", "status": "ready", "layer2": "Ran scan",
                         "category": "Automation", "macro_intent": "Improve scan", "step_count": 2,
                         "failure_count": 1, "rework_count": 1, "started_at": None}]
            if "FROM trajectory_steps" in sql:
                return [{"ordinal": 1, "tool": "run", "action_family": "execute",
                         "summary": "validation failed", "ok": False}]
            return ()

        def query_one(sql, _args):
            if "FROM workflows" in sql:
                return {"id": 2, "name": "Fact scan", "description": "", "status": "active",
                        "nodes": [], "trigger": {}}
            if "FROM trajectories" in sql:
                return {"id": 9, "prompt": "refine", "status": "ready", "layer1": "Run",
                        "layer2": "Ran", "category": "Automation", "macro_intent": "Improve",
                        "phases": [], "step_count": 1, "failure_count": 1, "rework_count": 1}

        events, _saved = run([
            {"action": "tool", "tool": "list_flows", "arguments": {}},
            {"action": "tool", "tool": "inspect_flow", "arguments": {"id": 2}},
            {"action": "tool", "tool": "list_workflow_observations", "arguments": {}},
            {"action": "tool", "tool": "inspect_workflow_observation", "arguments": {"id": 9}},
            {"action": "answer"},
        ], ["Separate the validation step."], dependencies(query=query, query_one=query_one))
        parsed = agent_evals.parse_events(events)
        self.assertEqual(
            [data["name"] for event, data in parsed if event == "tool_result"],
            ["list_flows", "inspect_flow", "list_workflow_observations", "inspect_workflow_observation"],
        )

    def test_final_answer_stream_is_not_buffered(self) -> None:
        produced = []

        def answer():
            produced.append("first")
            yield "first "
            produced.append("second")
            yield "second"

        stream, saved = run([{"action": "answer"}], answer())
        iterator = iter(stream)
        self.assertIn('"token": "first "', next(iterator))
        self.assertEqual(produced, ["first"])
        self.assertIn('"token": "second"', next(iterator))
        self.assertEqual(produced, ["first", "second"])
        tuple(iterator)
        self.assertEqual(saved[0][0], "first second")

    def test_search_then_read_uses_observed_document_id(self) -> None:
        document = {"id": 4, "title": "Retention runbook", "source": "confluence",
                    "author": "Dana", "updated_src": None,
                    "body": "Customer data is retained for 30 days.", "snippet": ""}
        events, _saved = run([
            {"action": "tool", "tool": "search", "arguments": {"query": "retention"}},
            {"action": "tool", "tool": "read_document", "arguments": {"id": 4}},
            {"action": "answer"},
        ], ["The runbook says 30 days."], dependencies(
            query=lambda _sql, _args: (), query_one=lambda _sql, _args: document,
            search=lambda _text, _limit: [{"id": 4, "title": "Retention runbook", "snippet": "30 days"}],
        ))
        parsed = agent_evals.parse_events(events)
        self.assertEqual(
            [data["name"] for event, data in parsed if event == "tool_result"],
            ["search", "read_document"],
        )


if __name__ == "__main__":
    unittest.main()

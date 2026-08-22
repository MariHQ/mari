from __future__ import annotations

import unittest

from mari_server.conversations import evals as agent_evals
from mari_server.conversations.routes import serialize_sse
from mari_components.agents.runtime import AgentPorts, stream_agent_turn
from mari_server.product.navigation import valid_navigation
from mari_server.conversations.tools import ToolDependencies, build_tool_bindings


class FakeToolStore:
    def __init__(self, *, document=None, sources=(), trajectories=(), trajectory=None,
                 trajectory_steps=(), answers=()):
        self._document = document
        self._sources = sources
        self._trajectories = trajectories
        self._trajectory = trajectory
        self._trajectory_steps = trajectory_steps
        self._answers = answers

    def document(self, _document_id):
        return self._document

    def document_tags(self, _document_id):
        return ()

    def sources(self):
        return self._sources

    def trajectories(self):
        return self._trajectories

    def trajectory(self, _trajectory_id):
        return self._trajectory

    def trajectory_steps(self, _trajectory_id):
        return self._trajectory_steps

    def answers(self):
        return self._answers


def dependencies(*, store=None, search=lambda _text, _limit: ()):
    return ToolDependencies(
        store=store or FakeToolStore(), search=search,
        record_search=lambda _text: None,
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
        deps = dependencies(
            store=FakeToolStore(
                sources=[{"id": 1, "display_name": "Confluence", "provider": "confluence",
                          "kind": "connector", "status": "active", "health": "Healthy", "docs_count": 50}],
                answers=[{"id": 4, "question": "How long?", "status": "approved", "served": 8}],
            ),
        )
        for case in agent_evals.TOOL_CASES:
            events, _saved = run([
                {"action": "tool", "tool": case.expected_tool, "arguments": {}},
                {"action": "answer"},
            ], [" ".join(case.answer_terms)], deps)
            with self.subTest(case=case.name):
                self.assertTrue(agent_evals.score_tool(case, events)["passed"])

    def test_workflow_refinement_uses_run_and_trajectory_evidence(self) -> None:
        store = FakeToolStore(
            trajectories=[{"id": 9, "prompt": "refine", "status": "ready", "layer2": "Ran scan",
                           "category": "Automation", "macro_intent": "Improve scan", "step_count": 2,
                           "failure_count": 1, "rework_count": 1, "started_at": None}],
            trajectory={"id": 9, "prompt": "refine", "status": "ready", "layer1": "Run",
                        "layer2": "Ran", "category": "Automation", "macro_intent": "Improve",
                        "phases": [], "step_count": 1, "failure_count": 1, "rework_count": 1},
            trajectory_steps=[{"ordinal": 1, "tool": "run", "action_family": "execute",
                               "summary": "validation failed", "ok": False}],
        )

        events, _saved = run([
            {"action": "tool", "tool": "list_workflow_observations", "arguments": {}},
            {"action": "tool", "tool": "inspect_workflow_observation", "arguments": {"id": 9}},
            {"action": "answer"},
        ], ["Separate the validation step."], dependencies(store=store))
        parsed = agent_evals.parse_sse_events(events)
        self.assertEqual(
            [data["name"] for event, data in parsed if event == "tool_result"],
            ["list_workflow_observations", "inspect_workflow_observation"],
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
            store=FakeToolStore(document=document),
            search=lambda _text, _limit: [{"id": 4, "title": "Retention runbook", "snippet": "30 days"}],
        ))
        parsed = agent_evals.parse_sse_events(events)
        self.assertEqual(
            [data["name"] for event, data in parsed if event == "tool_result"],
            ["search", "read_document"],
        )


if __name__ == "__main__":
    unittest.main()

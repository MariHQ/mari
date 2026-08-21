from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mari_server.conversations import routes, workflows
from mari_server.identity.context import AccessContext


ROW = {
    "id": 14, "name": "Ask about Mari", "description": "Answer what Mari is",
    "trajectory_prompt": "Ask about Mari", "match_threshold": 0.55,
    "phases": [{"start": 0, "end": 0}],
    "steps": [{"ordinal": 0, "tool": "search", "arguments": {"query": "what is mari?"}}],
    "cache_policy": "reviewed_answer", "cached_answer": "Mari manages product knowledge [1].",
}


class AssistantWorkflowCacheTests(unittest.TestCase):
    def test_exact_reviewed_trigger_needs_no_embedding(self):
        with patch.object(workflows.store, "active_workflows", return_value=[dict(ROW)]), \
             patch.object(workflows.store, "workflow_cache_state", return_value="fresh"), \
             patch.object(workflows.llm, "embed") as embed:
            selected = workflows.select("what is mari?", {"search"})
        self.assertEqual(selected["id"], 14)
        self.assertTrue(selected["match"]["exact"])
        embed.assert_not_called()

    def test_fresh_cache_wins_over_newer_uncached_duplicate_trigger(self):
        uncached = {**ROW, "id": 15, "cache_policy": "none"}
        with patch.object(workflows.store, "active_workflows",
                          return_value=[uncached, dict(ROW)]), \
             patch.object(workflows.store, "workflow_cache_state", return_value="fresh"), \
             patch.object(workflows.llm, "embed") as embed:
            selected = workflows.select("what is mari?", {"search"})
        self.assertEqual(selected["id"], 14)
        embed.assert_not_called()

    def test_cached_agent_response_never_iterates_the_model_loop(self):
        context = AccessContext(1, 1, "default", "Mari", "owner", frozenset())
        runtime = Mock()
        runtime.create_session.return_value = 8
        runtime.bindings.return_value = {}
        runtime.select_workflow.return_value = {**ROW, "match": {
            "workflow_score": 1.0, "phase_index": 0, "phase_score": 1.0,
            "step_index": 0, "step_score": 1.0,
        }}
        runtime.cached_workflow_response.return_value = {
            "answer": ROW["cached_answer"], "sources": [],
        }
        runtime.ports.return_value = SimpleNamespace()
        def forbidden_model_loop():
            raise AssertionError("cached responses must not enter the model loop")
            yield
        model_loop = Mock(return_value=forbidden_model_loop())

        async def read(response):
            return "".join([
                chunk.decode() if isinstance(chunk, bytes) else chunk
                async for chunk in response.body_iterator
            ])

        with patch.object(routes, "production_runtime", return_value=runtime), \
             patch.object(routes, "stream_agent_turn", model_loop):
            body = asyncio.run(read(routes.agent_chat(
                routes.AgentChatIn(message="what is mari?"), context,
            )))
        self.assertIn("workflow_selected", body)
        self.assertIn("Mari manages product knowledge", body)
        self.assertIn('"cache_hit": true', body)
        runtime.save_cached_workflow_response.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.identity import access
from mari_server.destinations import slack as bots


def slack_context() -> access.AccessContext:
    return access.external_access(
        7, "acme", "Acme", "slack", "installation-1",
        principals=frozenset({"channel:C1"}),
    )


class SlackKnowledgeScopeTests(unittest.TestCase):
    def test_project_approved_answer_is_served_to_slack(self) -> None:
        approved = {"question": "How do I deploy?", "answer": "Use the runbook.", "sim": 0.91}
        with access.use_access(slack_context()), \
             patch.object(bots.llm, "embed", return_value=[0.1, 0.2]), \
             patch.object(bots.bot_store, "approved_answer", return_value=approved), \
             patch.object(bots, "hybrid_search") as search:
            answer = bots.answer_question("How do I deploy?")
        self.assertIn("Use the runbook.", answer)
        self.assertIn("Approved answer", answer)
        search.assert_not_called()

    def test_verified_project_facts_ground_slack_generation(self) -> None:
        document = {"title": "Deploy", "source": "confluence", "body": "Run make deploy.", "snippet": ""}
        with access.use_access(slack_context()), \
             patch.object(bots.llm, "embed", return_value=None), \
             patch.object(bots, "hybrid_search", return_value=[document]), \
             patch.object(bots.bot_store, "verified_facts",
                          return_value=["Production deploys require approval."]), \
             patch.object(bots.llm, "generate_json", return_value={
                 "answer": "Use the approved deployment path [1].",
                 "confidence": 0.9,
                 "evidence": [{"document_id": "document:1", "quote": "Run make deploy."}],
             }) as generate:
            answer = bots.answer_question("How do I deploy?")
        self.assertIn("Sources: [1] Deploy", answer)
        self.assertIn("Production deploys require approval.", generate.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

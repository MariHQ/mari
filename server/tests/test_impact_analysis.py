from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.knowledge import graphql as mutations_knowledge
from mari_server.identity import access


PROJECT = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)

ROWS = [
    {"id": 11, "source": "docsite", "title": "Billing FAQ", "body": "Retention is 30 days."},
    {"id": 12, "source": "docs", "title": "Pricing policy", "body": "Plans inherit the retention window."},
    {"id": 13, "source": "slack", "title": "#pricing thread", "body": "Someone asked about retention."},
]


def run(answer, rows=ROWS):
    """Run the mutation over a canned search result and a canned model answer."""
    with access.use_access(PROJECT), \
         patch.object(mutations_knowledge, "hybrid_search", return_value=rows) as search, \
         patch.object(mutations_knowledge.llm, "generate_json", return_value=answer) as generate, \
         patch.object(mutations_knowledge, "audit"):
        result = mutations_knowledge.MutKnowledge().impact_analysis("retention is now 60 days")
    run.prompt = generate.call_args.args[0] if generate.call_args else ""
    run.timeout = generate.call_args.kwargs.get("timeout") if generate.call_args else None
    return result, search


class ImpactAnalysisTests(unittest.TestCase):
    def test_each_document_carries_its_own_verdict_and_id(self) -> None:
        result, search = run({
            "summary": "Three documents quote the retention window.",
            "documents": [
                {"document_id": "13", "severity": "minor", "reason": "asks about it in passing"},
                {"document_id": "11", "severity": "update-required", "reason": "states the old 30 days"},
                {"document_id": "12", "severity": "review", "reason": "inherits the window"},
            ],
        })

        # Recall budget: the analysis reads twelve candidates, not six.
        self.assertEqual(search.call_args.args[1], 12)
        # Strongest first, so the report's cap keeps what has to be acted on.
        self.assertEqual([(doc.title, doc.severity, doc.document_id) for doc in result.docs], [
            ("Billing FAQ", "update-required", 11),
            ("Pricing policy", "review", 12),
            ("#pricing thread", "minor", 13),
        ])
        self.assertEqual(result.docs[0].reason, "states the old 30 days")

    def test_small_model_output_is_tolerated_rather_than_dropped(self) -> None:
        result, _ = run({
            "summary": "Retention is quoted in one place.",
            "documents": [
                # A title instead of an id, a severity word off the scale, and
                # a row about a document that was never supplied.
                {"document_id": "Billing FAQ", "severity": "HIGH", "reason": "quotes 30 days"},
                {"document_id": "12", "reason": "no severity given"},
                {"document_id": "999", "severity": "review", "reason": "not in the corpus"},
            ],
        })

        self.assertEqual([(doc.document_id, doc.severity) for doc in result.docs],
                         [(11, "update-required"), (12, "minor")])

    def test_bare_id_list_from_the_old_prompt_still_answers(self) -> None:
        result, _ = run({"summary": "Two documents touched.", "affected_document_ids": ["11", "12"]})

        self.assertEqual([doc.document_id for doc in result.docs], [11, 12])
        self.assertEqual({doc.severity for doc in result.docs}, {"minor"})

    def test_report_is_capped_below_the_read(self) -> None:
        rows = [{"id": 100 + n, "source": "docs", "title": f"Doc {n}", "body": "Retention is 30 days."}
                for n in range(12)]
        result, _ = run({
            "summary": "Everything mentions retention.",
            "documents": [{"document_id": str(100 + n), "severity": "review", "reason": "mentions it"}
                          for n in range(12)],
        }, rows)

        self.assertEqual(len(result.docs), 8)

    def test_long_documents_reach_the_prompt_as_the_passage_about_the_claim(self) -> None:
        filler = "Mari indexes documents from every connected source. " * 400
        rows = [{"id": 21, "source": "docs", "title": "Handbook",
                 "body": f"{filler}The retention window is 30 days.{filler}"}]
        run({"summary": "One.", "documents": [
            {"document_id": "21", "severity": "update-required", "reason": "states 30 days"},
        ]}, rows)

        # The sentence the verdict rests on is in the prompt, and the 20,000
        # characters around it are not: twelve whole bodies is a prompt no
        # self-hosted model answers before the request gives up on it.
        self.assertIn("The retention window is 30 days.", run.prompt)
        self.assertLess(len(run.prompt), 4_000)
        self.assertEqual(run.timeout, mutations_knowledge.IMPACT_TIMEOUT_SECONDS)

    def test_a_document_sharing_no_word_with_the_claim_still_reaches_the_prompt(self) -> None:
        rows = [{"id": 22, "source": "docs", "title": "Brand voice",
                 "body": "We write plainly. " + ("Filler sentence here. " * 400)}]
        run({"summary": "One.", "documents": [
            {"document_id": "22", "severity": "minor", "reason": "mentions nothing"},
        ]}, rows)

        self.assertIn("We write plainly.", run.prompt)


if __name__ == "__main__":
    unittest.main()

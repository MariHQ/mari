"""One chat voice and one citation payload across dock, public chat and Slack."""

from __future__ import annotations

import datetime as dt
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from mari_server.conversations import citations
from mari_server.conversations.prompts import (
    CHAT_STYLE_RULES,
    FORMAT,
    NOT_FOUND,
    SURFACES,
    answer_system,
    default_style_text,
    workspace_style_text,
)

ROOT = Path(__file__).resolve().parents[2]
STYLE_DOC = ROOT / "docs" / "chat-style.md"
# The chat pack is seeded by 0024 and extended by later chat.* migrations;
# the fallback must agree with their union, not with the first file alone.
MIGRATIONS = (
    ROOT / "server" / "migrations" / "0024_chat_style_pack.sql",
    ROOT / "server" / "migrations" / "0027_chat_trust_rules.sql",
)

# One sentence that must reach the model on each surface, and nowhere else.
SURFACE_MARKERS = {
    "dock": "narrow panel beside the reader's work",
    "public": "read by people outside the workspace",
    "slack": "renders mrkdwn and not Markdown",
}


class ChatPromptTests(unittest.TestCase):
    def test_each_surface_gets_its_own_rule_and_only_its_own(self):
        for surface, marker in SURFACE_MARKERS.items():
            prompt = answer_system(None, surface)
            self.assertIn(marker, prompt, surface)
            for other, other_marker in SURFACE_MARKERS.items():
                if other != surface:
                    self.assertNotIn(other_marker, prompt, f"{surface} leaked {other}")

    def test_public_surface_treats_retrieved_context_as_authorized(self):
        prompt = answer_system(None, "public")
        self.assertIn("already been authorized", prompt)
        self.assertIn("restricted upstream sources", prompt)
        self.assertIn("answer from and cite it", prompt)
        self.assertIn("never open with the not-found sentence", prompt)

    def test_every_surface_carries_the_shared_style_and_the_untrusted_rule(self):
        self.assertEqual(set(SURFACES), set(SURFACE_MARKERS))
        for surface in SURFACES:
            prompt = answer_system(None, surface)
            self.assertIn("You are Mari", prompt)
            self.assertIn("untrusted data", prompt)
            for rule in CHAT_STYLE_RULES:
                self.assertIn(rule, prompt, surface)

    def test_workspace_rules_replace_the_shipped_ones_but_not_the_surface_rules(self):
        prompt = answer_system("- Always answer in haiku.", "slack")
        self.assertIn("Always answer in haiku.", prompt)
        self.assertNotIn(CHAT_STYLE_RULES[0], prompt)
        self.assertIn(SURFACE_MARKERS["slack"], prompt)

    def test_blank_workspace_rules_fall_back_to_the_shipped_ones(self):
        self.assertEqual(answer_system("   ", "dock"), answer_system(None, "dock"))
        self.assertIn(default_style_text(), answer_system(None, "dock"))

    def test_unknown_surface_is_a_programming_error_not_a_silent_default(self):
        with self.assertRaisesRegex(ValueError, "Unknown chat surface"):
            answer_system(None, "email")

    def test_workspace_style_text_reads_the_chat_pack_named_by_the_setting(self):
        asked: list[str] = []

        def style_rules(pack):
            asked.append(pack)
            return [{"description": "Answer in one word."}, {"description": "  "}]

        self.assertEqual(
            workspace_style_text(lambda _key: {"default_pack": "ai-slop"}, style_rules),
            "- Answer in one word.",
        )
        # default_pack governs written prose; the assistant follows chat_pack.
        self.assertEqual(asked, ["chat"])

        workspace_style_text(lambda _key: '{"chat_pack": "house-chat"}', style_rules)
        self.assertEqual(asked[-1], "house-chat")

    def test_an_unreadable_or_empty_pack_means_use_the_shipped_rules(self):
        def boom(_key):
            raise RuntimeError("no database yet")

        self.assertIsNone(workspace_style_text(boom, lambda _pack: []))
        self.assertIsNone(workspace_style_text(lambda _key: None, lambda _pack: []))


class ChatStylePackTests(unittest.TestCase):
    def test_the_shipped_pack_and_the_baked_fallback_say_the_same_thing(self):
        sql = "\n".join(m.read_text(encoding="utf-8") for m in MIGRATIONS).replace("''", "'")
        for rule in CHAT_STYLE_RULES:
            self.assertIn(rule, sql, rule)
        self.assertIn("'chat', 'Chat answers'", sql)

    def test_the_style_guide_document_exists_and_covers_the_hard_rules(self):
        raw = STYLE_DOC.read_text(encoding="utf-8")
        doc = " ".join(raw.split())  # the guide hard-wraps; the sentences do not
        self.assertIn("I could not find this in the connected sources", doc)
        self.assertIn("mrkdwn", doc)
        # The guide must obey its own rule about dashes.
        self.assertNotRegex(raw, r"[–—]")


def row(**overrides):
    base = {
        "id": 41, "source": "confluence", "kind": "page", "title": "Deploy runbook",
        "snippet": "", "body": "# Deploy runbook\n\nDeploys run **every Tuesday** at 10:00 UTC.",
        "author": "Dana Ortiz", "author_initials": "DO",
        "updated_src": dt.datetime(2026, 8, 1, 9, 30), "tags": ["canonical"], "score": 4.0,
    }
    base.update(overrides)
    return base


class CitationPayloadTests(unittest.TestCase):
    def test_payload_carries_every_field_the_source_card_renders(self):
        [source] = citations.source_payload([row()], source_urls={41: "https://wiki/deploy"})
        self.assertEqual(source, {
            "n": 1, "source": "confluence", "kind": "page", "title": "Deploy runbook",
            "snippet": "Deploys run every Tuesday at 10:00 UTC.",
            "meta": "Deploys run every Tuesday at 10:00 UTC.",
            "author": "Dana Ortiz", "updated": "2026-08-01T09:30:00",
            "tags": ["canonical"], "document_id": 41,
            "href": "/knowledge/doc?id=41",
            "source_url": "https://wiki/deploy", "score": 1.0,
        })

    def test_meta_stays_an_alias_of_snippet_for_old_clients(self):
        for source in citations.source_payload([row(), row(id=42, title="Oncall")]):
            self.assertEqual(source["meta"], source["snippet"])

    def test_snippet_is_cleaned_prose_and_stops_on_a_word(self):
        long_body = "Deploys " + "run steadily every single Tuesday morning " * 12
        [source] = citations.source_payload([row(body=long_body)])
        self.assertLessEqual(len(source["snippet"]), citations.SNIPPET_LIMIT)
        self.assertFalse(source["snippet"].endswith("ru"))
        self.assertNotIn("#", source["snippet"])

    def test_a_row_with_no_body_still_gets_a_snippet_from_the_stored_one(self):
        [source] = citations.source_payload([row(body="", snippet="Stored preview text.")])
        self.assertEqual(source["snippet"], "Stored preview text.")

    def test_duplicate_documents_are_cited_once_keeping_the_first_number(self):
        sources = citations.source_payload([row(), row(title="Deploy runbook (copy)"), row(id=42)])
        self.assertEqual([s["n"] for s in sources], [1, 2])
        self.assertEqual([s["document_id"] for s in sources], [41, 42])
        self.assertEqual(sources[0]["title"], "Deploy runbook")

    def test_scores_are_normalized_against_the_best_hit_in_the_answer(self):
        sources = citations.source_payload(
            [row(score=4.0), row(id=42, score=1.0), row(id=43, score=0.0)])
        self.assertEqual([s["score"] for s in sources], [1.0, 0.25, 0.0])
        for source in sources:
            self.assertGreaterEqual(source["score"], 0.0)
            self.assertLessEqual(source["score"], 1.0)

    def test_all_zero_scores_do_not_divide_by_zero(self):
        sources = citations.source_payload([row(score=0.0), row(id=42, score=None)])
        self.assertEqual([s["score"] for s in sources], [0.0, 0.0])

    def test_source_url_is_null_unless_it_is_actually_a_link(self):
        [source] = citations.source_payload([row()], source_urls={41: "docs/runbook.md"})
        self.assertIsNone(source["source_url"])
        [source] = citations.source_payload([row()])
        self.assertIsNone(source["source_url"])
        [source] = citations.source_payload([row(source_path="https://github.com/a/b")])
        self.assertEqual(source["source_url"], "https://github.com/a/b")

    def test_missing_or_odd_fields_never_break_the_payload(self):
        [source] = citations.source_payload([{"id": 9, "title": "Bare"}])
        self.assertEqual(source["kind"], "page")
        self.assertEqual(source["tags"], [])
        self.assertEqual(source["updated"], "")
        self.assertEqual(source["author"], "")
        self.assertEqual(source["snippet"], "")


class NotFoundSentenceTests(unittest.TestCase):
    def test_the_constant_is_the_sentence_the_style_rules_ask_for(self):
        self.assertTrue(any(NOT_FOUND in rule for rule in CHAT_STYLE_RULES))

    def test_every_surface_tells_the_model_not_to_wrap_the_answer(self):
        for surface in SURFACES:
            self.assertIn(FORMAT, answer_system(None, surface), surface)
        # Above the style pack, so a workspace rule cannot switch it off.
        self.assertIn(FORMAT, answer_system("- Always answer in haiku.", "dock"))

    def test_the_format_rule_governs_the_opening_and_still_allows_fenced_code(self):
        # It must not contradict the style rule "Put code in a fenced block".
        self.assertIn("Never open the answer with a code fence", FORMAT)
        self.assertIn("fenced block with a language tag", FORMAT)
        self.assertNotIn("never wrap the answer", FORMAT)


class CitedSourcesTests(unittest.TestCase):
    """The rail under an answer is what the answer cites, not what retrieval
    happened to hand the model: a "could not find" answer arrived over four
    MongoDB playbooks that had nothing to do with the question."""

    def sources(self):
        return citations.source_payload([row(), row(id=42, title="Oncall"), row(id=43, title="Cluster")])

    def test_cite_numbers_reads_markers_outside_code(self):
        answer = "Deploys run Tuesday [1]. See `arr[2]` and:\n```sh\necho [3]\n```\nAlso [3](https://x) and [1][3]."
        self.assertEqual(citations.cite_numbers(answer), {1, 3})
        self.assertEqual(citations.cite_numbers(""), set())

    def test_cited_keeps_only_the_rows_the_answer_cites_with_their_numbers(self):
        cited = citations.cited("Page one says so [1], and so does three [3].", self.sources())
        self.assertEqual([s["n"] for s in cited], [1, 3])
        self.assertEqual([s["title"] for s in cited], ["Deploy runbook", "Cluster"])

    def test_a_not_found_answer_cites_nothing(self):
        for answer in (
            "I could not find this in the connected sources.",
            "  I could not find this in the connected sources",
            "```\nI could not find this in the connected sources.\n```",
            "`I could not find this in the connected sources.`",
            "Sorry, I could not find this in the connected sources!",
        ):
            self.assertTrue(citations.is_not_found(answer), answer)
            self.assertEqual(citations.cited(answer, self.sources()), [], answer)

    def test_a_paraphrased_refusal_cites_nothing(self):
        # The customer's original symptom: the model reworded the sentence
        # and the rail came back with every candidate.
        for answer in (
            "I couldn't find anything about that in the connected sources.",
            "I could not find any information on retention in the context provided.",
            "There is no information about retention in the knowledge base.",
            "The sources say nothing about retention.",
            "Retention is not covered in the connected sources.",
            "I cannot find that in the context.",
            "I couldn\u2019t find anything about that.",
            "Nothing about that, sorry.",
        ):
            self.assertTrue(citations.is_not_found(answer), answer)
            self.assertEqual(citations.cited(answer, self.sources()), [], answer)

    def test_a_long_answer_that_mentions_a_gap_in_passing_is_not_a_refusal(self):
        answer = ("Retention is 30 days for logs and 90 days for metrics, and backups roll "
                  "over weekly. The retention job runs nightly and the alerts page shows "
                  "when it last ran. I couldn't find who owns the job, though.")
        self.assertGreater(len(answer), citations.REFUSAL_LIMIT)
        self.assertFalse(citations.is_not_found(answer))

    def test_not_found_prefix_followed_by_a_grounded_answer_keeps_sources(self):
        answer = (
            "I could not find this in the connected sources beyond the two messages shown: "
            '"the sky is blue" and "this is a private test."'
        )
        self.assertFalse(citations.is_not_found(answer))
        self.assertEqual(len(citations.cited(answer, self.sources())), 3)
        self.assertEqual(len(citations.cited(answer, self.sources())), 3)

    def test_a_cited_answer_is_never_a_refusal_however_it_hedges(self):
        answer = "Retention is 30 days [1]. Encryption at rest is not covered by the sources."
        self.assertEqual([s["n"] for s in citations.cited(answer, self.sources())], [1])

    def test_markers_that_resolve_to_no_candidate_are_read_as_no_markers(self):
        # [7] and [0] over three candidates: a slip, not a citation.
        self.assertEqual(len(citations.cited("Deploys run Tuesday [7] [0].", self.sources())), 3)
        self.assertEqual(citations.cited("I could not find this in the connected sources [7].",
                                         self.sources()), [])
        # A real marker beside a slip still narrows to the real one.
        self.assertEqual([s["n"] for s in citations.cited("See [2] and [9].", self.sources())], [2])

    def test_an_uncited_real_answer_keeps_every_candidate(self):
        # The model drew on the context without saying where; hiding all
        # provenance would be worse. This is also what keeps an approved
        # answer's card, which nothing cites by number.
        self.assertFalse(citations.is_not_found("Deploys run every Tuesday."))
        self.assertEqual(len(citations.cited("Deploys run every Tuesday.", self.sources())), 3)

    def test_clean_answer_drops_leading_indentation_the_renderer_would_read_as_code(self):
        self.assertEqual(citations.clean_answer("    Deploys run Tuesday [1]."), "Deploys run Tuesday [1].")
        self.assertEqual(citations.clean_answer("\n\n\tI could not find this in the connected sources."),
                         "I could not find this in the connected sources.")

    def test_clean_answer_unwraps_only_a_wrapped_not_found_sentence(self):
        sentence = "I could not find this in the connected sources."
        self.assertEqual(citations.clean_answer(f"```\n{sentence}\n```"), sentence)
        self.assertEqual(citations.clean_answer(f"```text\n{sentence}\n```\n"), sentence + "\n")
        self.assertEqual(citations.clean_answer(f"~~~\n{sentence}\n~~~"), sentence)
        self.assertEqual(citations.clean_answer(f"`{sentence}`"), sentence)
        code = "```python\nprint(1)\n```"
        self.assertEqual(citations.clean_answer(code), code)
        self.assertEqual(citations.clean_answer("Run `make` [1]."), "Run `make` [1].")
        self.assertEqual(citations.clean_answer("`config.yml` is not in the sources [1]."),
                         "`config.yml` is not in the sources [1].")

    def test_clean_answer_unwraps_a_fenced_not_found_sentence_the_model_then_continued(self):
        # What follows the wrapper is kept as written, so the stream (which
        # releases at the closed fence) and the stored transcript agree.
        sentence = "I could not find this in the connected sources."
        self.assertEqual(citations.clean_answer(f"```\n{sentence}\n```\nThat said, deploys run Tuesday [1]."),
                         f"{sentence}\nThat said, deploys run Tuesday [1].")
        self.assertEqual(citations.clean_answer(f"`{sentence}` However, see [1]."),
                         f"{sentence} However, see [1].")
        # A fence that is not a wrapper around the sentence is left alone.
        code = "```sh\necho hi\n```\nRun it [1]."
        self.assertEqual(citations.clean_answer(code), code)


class SlackSourceLineTests(unittest.TestCase):
    def test_a_slack_source_names_the_connector_not_just_the_title(self):
        from mari_server.destinations import slack

        self.assertEqual(slack._source_label("Deploy runbook", "confluence"),
                         "Deploy runbook (confluence)")
        self.assertEqual(slack._source_label("Verified facts", ""), "Verified facts")

    def test_slack_prompt_forbids_markdown_headings(self):
        from mari_server.destinations import slack

        with patch.object(slack, "workspace_style_text", return_value=None):
            self.assertIn(SURFACE_MARKERS["slack"], slack.bot_system())


class StyleDocumentShapeTests(unittest.TestCase):
    def test_the_guide_stays_short_enough_to_read(self):
        lines = STYLE_DOC.read_text(encoding="utf-8").splitlines()
        self.assertLess(len(lines), 120)
        self.assertTrue(re.match(r"# Chat answer style", lines[0]))


if __name__ == "__main__":
    unittest.main()

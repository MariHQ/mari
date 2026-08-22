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
    SURFACES,
    answer_system,
    default_style_text,
    workspace_style_text,
)

ROOT = Path(__file__).resolve().parents[2]
STYLE_DOC = ROOT / "docs" / "chat-style.md"
MIGRATION = ROOT / "server" / "migrations" / "0018_chat_style_pack.sql"

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
        sql = MIGRATION.read_text(encoding="utf-8").replace("''", "'")
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

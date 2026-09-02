"""Body rendering and transport behaviour for the Atlassian connectors,
locking in the 2026-08-23 sweep fixes: storage_to_text keeps code samples and
drops macro configuration, ADF surfaces every visible node, and the shared
transport honours date-form Retry-After and reports validation error kinds."""
from __future__ import annotations

import json
import unittest

from mari_components.connectors._shared import send
from mari_components.connectors.confluence import (
    ConfluenceConfig,
    _document,
    _order_key,
    storage_to_text,
    validate_confluence,
)
from mari_components.connectors.jira import JiraConfig, _text, poll_jira, validate_jira
from mari_components.errors import RateLimitFailure, TransientFailure
from mari_components.http import HttpRequest, HttpResponse
from mari_components.types import PollRequest


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        value = self.responses.pop(0)
        return value if isinstance(value, HttpResponse) else HttpResponse(200, {}, json.dumps(value).encode())


class StorageTextTests(unittest.TestCase):
    def test_code_macro_body_containing_markup_survives(self):
        xhtml = ('<ac:structured-macro ac:name="code">'
                 '<ac:plain-text-body><![CDATA[<div class="a">hi</div>]]></ac:plain-text-body>'
                 '</ac:structured-macro>')
        self.assertIn('<div class="a">hi</div>', storage_to_text(xhtml))

    def test_code_macro_parameters_do_not_leak_into_the_body(self):
        xhtml = ('<ac:structured-macro ac:name="code">'
                 '<ac:parameter ac:name="language">python</ac:parameter>'
                 '<ac:plain-text-body><![CDATA[print(1)]]></ac:plain-text-body>'
                 '</ac:structured-macro>')
        text = storage_to_text(xhtml)
        self.assertNotIn("python", text)
        self.assertIn("print(1)", text)

    def test_title_parameter_is_kept_but_separated(self):
        xhtml = ('<p>Release: </p><ac:structured-macro ac:name="status">'
                 '<ac:parameter ac:name="colour">Green</ac:parameter>'
                 '<ac:parameter ac:name="title">SHIPPED</ac:parameter>'
                 '</ac:structured-macro>')
        text = storage_to_text(xhtml)
        self.assertIn("SHIPPED", text)
        self.assertNotIn("Green", text)
        self.assertNotIn("GreenSHIPPED", text)

    def test_pre_code_block_is_balanced(self):
        text = storage_to_text("<pre><code>alpha</code></pre>")
        self.assertIn("alpha", text)
        self.assertEqual(text.count("```") % 2, 0, text)

    def test_unrelated_macro_end_does_not_close_an_open_code_span(self):
        xhtml = ('<p><code>x<ac:structured-macro ac:name="info">'
                 '<ac:rich-text-body>note</ac:rich-text-body></ac:structured-macro>y</code></p>')
        self.assertNotIn("```", storage_to_text(xhtml))

    def test_image_alt_text_is_indexed(self):
        xhtml = ('<p><ac:image ac:alt="Architecture diagram">'
                 '<ri:attachment ri:filename="arch.png" /></ac:image></p>')
        self.assertIn("Architecture diagram", storage_to_text(xhtml))

    def test_page_link_target_is_indexed(self):
        xhtml = ('<p>See <ac:link><ri:page ri:content-title="Runbook" />'
                 '<ac:plain-text-link-body><![CDATA[the runbook]]></ac:plain-text-link-body>'
                 '</ac:link></p>')
        text = storage_to_text(xhtml)
        self.assertIn("the runbook", text)
        self.assertIn("Runbook", text)

    def test_task_status_does_not_glue_onto_task_text(self):
        xhtml = ('<ac:task-list><ac:task><ac:task-status>complete</ac:task-status>'
                 '<ac:task-body>Seed the space</ac:task-body></ac:task></ac:task-list>')
        self.assertNotIn("completeSeed", storage_to_text(xhtml))

    def test_adf_panel_attributes_do_not_leak_into_the_panel_text(self):
        # The new editor's note panel, as Confluence Cloud stores it: the
        # panel type and a local id are attribute nodes, the words are in
        # adf-content, and adf-fallback repeats them for old editors.
        body = "<p>As of November 2024, this playbook is specific to Atlas MongoDB.</p>"
        xhtml = ('<ac:adf-extension><ac:adf-node type="panel">'
                 '<ac:adf-attribute key="panel-type">note</ac:adf-attribute>'
                 '<ac:adf-attribute key="local-id">f19de4a5-4510-49ad-ab12-f4c53cd39a52</ac:adf-attribute>'
                 f'<ac:adf-content>{body}</ac:adf-content>'
                 '</ac:adf-node>'
                 f'<ac:adf-fallback><div class="panel">{body}</div></ac:adf-fallback>'
                 '</ac:adf-extension><p>Check the cluster first.</p>')
        text = storage_to_text(xhtml)
        self.assertTrue(text.startswith("As of November 2024"), text)
        self.assertNotIn("note", text)
        self.assertNotIn("f19de4a5", text)
        self.assertEqual(text.count("As of November 2024"), 1, text)
        self.assertNotIn("MongoDB.Check", text)
        self.assertIn("Check the cluster first.", text)

    def test_adf_fallback_is_read_when_the_node_has_no_content(self):
        xhtml = ('<ac:adf-extension><ac:adf-node type="decision-list">'
                 '<ac:adf-attribute key="local-id">1b0c</ac:adf-attribute>'
                 '</ac:adf-node>'
                 '<ac:adf-fallback><p>Decided: ship on Tuesday.</p></ac:adf-fallback>'
                 '</ac:adf-extension>')
        text = storage_to_text(xhtml)
        self.assertEqual(text, "Decided: ship on Tuesday.")

    def test_expand_title_is_kept_and_separated_from_its_body(self):
        # An expand's heading is the one ADF attribute a reader sees.
        xhtml = ('<ac:adf-extension><ac:adf-node type="expand">'
                 '<ac:adf-attribute key="title">Rollback steps</ac:adf-attribute>'
                 '<ac:adf-attribute key="local-id">9c1d</ac:adf-attribute>'
                 '<ac:adf-content><p>Run the revert playbook.</p></ac:adf-content>'
                 '</ac:adf-node>'
                 '<ac:adf-fallback><div class="expand-container"><div class="expand-control">'
                 'Rollback steps</div><p>Run the revert playbook.</p></div></ac:adf-fallback>'
                 '</ac:adf-extension>')
        text = storage_to_text(xhtml)
        self.assertIn("Rollback steps", text)
        self.assertIn("Run the revert playbook.", text)
        self.assertNotIn("stepsRun", text)
        self.assertNotIn("9c1d", text)
        self.assertEqual(text.count("Run the revert playbook."), 1, text)

    def test_cdata_only_content_still_counts_as_the_node_rendering(self):
        xhtml = ('<ac:adf-extension><ac:adf-node type="codeBlock">'
                 '<ac:adf-content><![CDATA[print(1)]]></ac:adf-content>'
                 '</ac:adf-node>'
                 '<ac:adf-fallback><pre>print(1)</pre></ac:adf-fallback>'
                 '</ac:adf-extension>')
        text = storage_to_text(xhtml)
        self.assertEqual(text.count("print(1)"), 1, text)

    def test_image_alt_and_page_title_content_still_count_as_the_node_rendering(self):
        for inner in ('<ac:image ac:alt="Architecture diagram"><ri:attachment ri:filename="a.png" /></ac:image>',
                      '<ac:link><ri:page ri:content-title="Architecture diagram" /></ac:link>'):
            xhtml = ('<ac:adf-extension><ac:adf-node type="mediaSingle">'
                     f'<ac:adf-content>{inner}</ac:adf-content></ac:adf-node>'
                     '<ac:adf-fallback><p>Architecture diagram</p></ac:adf-fallback>'
                     '</ac:adf-extension>')
            text = storage_to_text(xhtml)
            self.assertEqual(text.count("Architecture diagram"), 1, text)

    def test_a_nested_extension_marks_its_parent_as_rendered(self):
        body = "<p>Ship on Tuesday.</p>"
        inner = ('<ac:adf-extension><ac:adf-node type="panel">'
                 f'<ac:adf-content>{body}</ac:adf-content></ac:adf-node>'
                 f'<ac:adf-fallback>{body}</ac:adf-fallback></ac:adf-extension>')
        xhtml = ('<ac:adf-extension><ac:adf-node type="layoutSection">'
                 f'<ac:adf-content>{inner}</ac:adf-content></ac:adf-node>'
                 f'<ac:adf-fallback>{body}</ac:adf-fallback></ac:adf-extension>')
        text = storage_to_text(xhtml)
        self.assertEqual(text.count("Ship on Tuesday."), 1, text)

    def test_an_unclosed_adf_attribute_does_not_swallow_the_rest_of_the_page(self):
        xhtml = ('<ac:adf-extension><ac:adf-node type="panel">'
                 '<ac:adf-attribute key="panel-type">note'
                 '<ac:adf-content><p>Inside the panel.</p></ac:adf-content>'
                 '</ac:adf-node></ac:adf-extension>'
                 '<p>After the panel.</p>')
        text = storage_to_text(xhtml)
        self.assertIn("After the panel.", text)
        self.assertNotIn("note", text)

    def test_placeholder_hint_is_not_indexed(self):
        xhtml = ('<p><ac:placeholder>Type / to insert content</ac:placeholder></p>'
                 '<p>Real text</p>')
        text = storage_to_text(xhtml)
        self.assertNotIn("Type /", text)
        self.assertIn("Real text", text)


class AdfTextTests(unittest.TestCase):
    def test_code_block_does_not_run_into_the_next_paragraph(self):
        adf = {"type": "doc", "content": [
            {"type": "codeBlock", "content": [{"type": "text", "text": "print(1)"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Then restart."}]}]}
        self.assertNotIn("print(1)Then restart.", _text(adf))

    def test_hard_break_becomes_a_newline(self):
        adf = {"type": "paragraph", "content": [
            {"type": "text", "text": "one"}, {"type": "hardBreak"},
            {"type": "text", "text": "two"}]}
        self.assertNotIn("onetwo", _text(adf))

    def test_table_cells_are_separated(self):
        adf = {"type": "table", "content": [{"type": "tableRow", "content": [
            {"type": "tableCell", "content": [{"type": "paragraph",
             "content": [{"type": "text", "text": "prod"}]}]},
            {"type": "tableCell", "content": [{"type": "paragraph",
             "content": [{"type": "text", "text": "ana"}]}]}]}]}
        self.assertNotIn("prodana", _text(adf).replace("\n", ""))

    def test_link_mark_keeps_its_href(self):
        adf = {"type": "paragraph", "content": [{"type": "text", "text": "runbook",
               "marks": [{"type": "link", "attrs": {"href": "https://example.com/rb"}}]}]}
        self.assertIn("example.com/rb", _text(adf))

    def test_mention_text_is_kept(self):
        adf = {"type": "paragraph", "content": [
            {"type": "text", "text": "Ping "},
            {"type": "mention", "attrs": {"id": "1", "text": "@ana"}}]}
        self.assertIn("@ana", _text(adf))

    def test_list_items_keep_a_marker(self):
        adf = {"type": "bulletList", "content": [
            {"type": "listItem", "content": [{"type": "paragraph",
             "content": [{"type": "text", "text": "alpha"}]}]}]}
        self.assertIn("- alpha", _text(adf))


class TransportBehaviourTests(unittest.TestCase):
    def test_jira_site_url_without_a_scheme_is_usable(self):
        http = FakeHttp([{"emailAddress": "me@example.com"}])
        validate_jira(JiraConfig("company.atlassian.net", "me@example.com", "t"), http=http)
        self.assertTrue(http.requests[0].url.startswith("https://company.atlassian.net/"))

    def test_confluence_source_url_lives_under_wiki(self):
        page = {"id": "30670884", "title": "Glossary",
                "body": {"storage": {"value": "<p>x</p>"}}, "version": {"number": 1},
                "history": {"lastUpdated": {"when": "2026-01-01T00:00:00.000Z"}},
                "_links": {"webui": "/spaces/FERN/pages/30670884/Glossary"}}
        document = _document(page, "https://mari-hq.atlassian.net")
        self.assertEqual(document.source_url,
                         "https://mari-hq.atlassian.net/wiki/spaces/FERN/pages/30670884/Glossary")

    def test_numeric_page_ids_order_numerically(self):
        when = "2026-08-18T02:21:06.883000Z"
        self.assertGreater(_order_key(when, "1000"), _order_key(when, "999"))

    def test_second_precision_timestamps_order_as_instants(self):
        self.assertLess(_order_key("2026-01-01T00:00:07Z", "1"),
                        _order_key("2026-01-01T00:00:07.500000Z", "1"))

    def test_is_last_false_without_a_token_terminates(self):
        http = FakeHttp([{"issues": [{"key": "KAN-1", "fields": {
            "summary": "s", "updated": "2026-01-01T00:00:00.000+0000"}}],
            "isLast": False}] * 30)
        pages = list(poll_jira(JiraConfig("https://x.atlassian.net", "e", "t", project_key="KAN"),
                               PollRequest(page_limit=20), http=http))
        self.assertLess(len(http.requests), 20)
        self.assertFalse(pages[-1].snapshot_complete)

    def test_retry_after_http_date_is_honoured(self):
        def limited(_request):
            return HttpResponse(429, {"Retry-After": "Wed, 21 Oct 2093 07:28:00 GMT"}, b"{}")
        with self.assertRaises(RateLimitFailure) as caught:
            send(limited, HttpRequest("GET", "https://x/y"))
        self.assertIsNotNone(caught.exception.retry_after)
        self.assertGreater(caught.exception.retry_after, 0)

    def test_validation_failure_reports_its_error_kind(self):
        def dead(_request):
            raise ConnectionError("connection refused")
        result = validate_confluence(
            ConfluenceConfig("https://x.atlassian.net", "e", "t"), http=dead)
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, "transient")
        result = validate_jira(JiraConfig("https://x.atlassian.net", "e", "t"), http=dead)
        self.assertEqual(result.kind, "transient")


if __name__ == "__main__":
    unittest.main(verbosity=2)

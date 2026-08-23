"""Cursor, checkpoint, and sweep-termination behaviour for the Atlassian
connectors. These lock in the 2026-08-23 fixes: Jira cursors render as JQL
date literals, checkpoints never leak into cursors, and Confluence sweeps
end on the absence of a next link, not on a short window."""
from __future__ import annotations

import json
import unittest
import urllib.parse

from mari_components.connectors.confluence import ConfluenceConfig, poll_confluence
from mari_components.connectors.jira import JiraConfig, poll_jira
from mari_components.http import HttpResponse
from mari_components.sync import ManifestEntry, SyncState, plan_sync
from mari_components.sync.ingestion import AppliedPage, consume_connector_pages
from mari_components.types import PollRequest, SyncMode

JIRA = JiraConfig("https://example.atlassian.net", "me@example.com", "secret", project_key="KAN")
CONFLUENCE = ConfluenceConfig("https://example.atlassian.net", "me@example.com", "secret")


def jql_of(request) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)["jql"][0]


def jira_response(*issues, is_last=True, token=None) -> dict:
    return {"issues": list(issues), "isLast": is_last,
            **({"nextPageToken": token} if token else {})}


def issue(key: str, updated: str) -> dict:
    return {"key": key, "fields": {"summary": key, "updated": updated}}


class JiraCursorTests(unittest.TestCase):
    def test_cursor_is_rendered_as_a_jql_date_literal(self):
        # Jira's JQL parser rejects the raw ISO timestamp the API returns
        # ("2026-05-09T22:24:06.157-0400"); /search/jql does not even error,
        # it returns 200 with zero issues, so incremental sync silently dies.
        http = FakeHttp([jira_response()])
        list(poll_jira(JIRA, PollRequest(cursor="2026-05-09T22:24:06.157-0400"), http=http))
        jql = jql_of(http.requests[0])
        self.assertIn('updated >= "2026-05-09 22:24"', jql)
        self.assertNotIn("22:24:06", jql)
        self.assertNotIn("-0400", jql)

    def test_a_non_timestamp_cursor_is_not_sent_to_jira(self):
        # Older releases persisted the opaque nextPageToken as the cursor;
        # sending it as a date literal is HTTP 400 on every later run.
        http = FakeHttp([jira_response()])
        list(poll_jira(JIRA, PollRequest(cursor="CAEQAY"), http=http))
        jql = jql_of(http.requests[0])
        self.assertNotIn("CAEQAY", jql)
        self.assertNotIn("updated >=", jql)

    def test_the_newest_cursor_is_chosen_by_instant_not_by_string(self):
        # Across the DST flip -0400/-0500 the lexically larger wall clock is
        # the older instant.
        http = FakeHttp([jira_response(
            issue("KAN-1", "2026-11-01T01:30:00.000-0400"),
            issue("KAN-2", "2026-11-01T01:15:00.000-0500"),
        )])
        pages = list(poll_jira(JIRA, PollRequest(), http=http))
        self.assertEqual(pages[-1].next_cursor, "2026-11-01T01:15:00.000-0500")

    def test_an_unfinished_sweep_holds_the_cursor_and_carries_a_checkpoint(self):
        http = FakeHttp([jira_response(issue("KAN-1", "2026-06-01T00:00:00.000+0000"),
                                       is_last=False, token="CAEQAY")])
        pages = list(poll_jira(JIRA, PollRequest(cursor="2026-05-09T22:24:06.157-0400",
                                                 page_limit=1), http=http))
        state = SyncState(cursor="2026-05-09T22:24:06.157-0400")
        for page in pages:
            state = plan_sync(state, page, mode=SyncMode.INCREMENTAL).state
        self.assertEqual(state.cursor, "2026-05-09T22:24:06.157-0400")
        self.assertEqual(state.checkpoint, "CAEQAY")


def confluence_page(n: int, when: str) -> dict:
    return {"id": str(1000 + n), "title": f"Page {n}", "type": "page",
            "body": {"storage": {"value": f"<p>body {n}</p>"}},
            "version": {"number": 1},
            "history": {"lastUpdated": {"when": when}},
            "space": {"key": "ENG"},
            "_links": {"webui": f"/spaces/ENG/pages/{1000 + n}"}}


class ConfluenceSite:
    """A Confluence that caps `limit` at a server maximum and can filter rows
    out of a window after applying the limit, exactly like Cloud permission
    filtering does."""

    def __init__(self, rows, server_cap=100, drop_from_first_window=0):
        self.rows, self.cap, self.drop = rows, server_cap, drop_from_first_window
        self.calls: list[tuple[int, int]] = []

    def __call__(self, request):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(request.url).query))
        start = int(query.get("start", 0))
        limit = min(int(query.get("limit", 25)), self.cap)
        self.calls.append((start, limit))
        window = self.rows[start:start + limit]
        if start == 0 and self.drop:
            window = window[: max(0, limit - self.drop)]
        more = start + limit < len(self.rows)
        body = {"results": window, "start": start, "limit": limit, "size": len(window),
                "_links": ({"next": f"/rest/api/content?start={start + limit}&limit={limit}"}
                           if more else {})}
        return HttpResponse(200, {}, json.dumps(body).encode())


def full_sync_deletions(pages, manifest_ids) -> list[str]:
    gone: list[str] = []

    def apply_page(plan, _n):
        gone.extend(t.external_id for t in plan.deletes)
        return AppliedPage()

    consume_connector_pages(
        pages,
        SyncState(cursor=None, manifest={i: ManifestEntry("old") for i in manifest_ids}),
        SyncMode.FULL, apply_page=apply_page)
    return gone


class ConfluenceSweepTests(unittest.TestCase):
    def test_server_capped_limit_does_not_end_the_sweep_early(self):
        rows = [confluence_page(n, f"2026-01-{n % 28 + 1:02d}T00:00:00.000Z") for n in range(250)]
        http = ConfluenceSite(rows, server_cap=100)
        pages = list(poll_confluence(CONFLUENCE, PollRequest(page_size=200), http=http))
        seen = {d.external_id for p in pages for d in p.upserts}
        self.assertEqual(len(seen), 250, f"swept {len(seen)} of 250, calls={http.calls}")
        self.assertTrue(pages[-1].snapshot_complete)

    def test_a_short_filtered_window_with_a_next_link_is_not_terminal(self):
        # Confluence applies `limit` before permission filtering, so an
        # intermediate window can be short while _links.next still points on.
        # Ending there declared a complete snapshot over a fraction of the
        # site, and a full sync then deleted everything unreached.
        http = ConfluenceSite([confluence_page(n, "2026-01-01T00:00:00.000Z") for n in range(300)],
                              drop_from_first_window=40)
        pages = list(poll_confluence(CONFLUENCE, PollRequest(page_size=100), http=http))
        seen = {d.external_id for p in pages for d in p.upserts}
        self.assertEqual(len(seen), 260)
        gone = full_sync_deletions(
            poll_confluence(CONFLUENCE, PollRequest(page_size=100),
                            http=ConfluenceSite(
                                [confluence_page(n, "2026-01-01T00:00:00.000Z") for n in range(300)],
                                drop_from_first_window=40)),
            [str(1000 + n) for n in range(300)])
        # Only the 40 permission-filtered rows may be tombstoned, never the
        # windows behind a premature terminal.
        self.assertLessEqual(len(gone), 40, f"deleted {len(gone)} pages the sweep never reached")

    def test_a_resumed_incremental_keeps_the_callers_cursor_as_the_filter(self):
        # The checkpoint's high-water mark is not the change filter: the sweep
        # is unordered, so a later window can hold pages updated after the
        # caller's cursor but below the mark.
        old = [confluence_page(n, "2026-01-01T00:00:00.000Z") for n in range(99)]
        rows = (old + [confluence_page(500, "2026-06-01T00:00:00.000Z")]
                + [confluence_page(700, "2026-03-01T00:00:00.000Z")]
                + [confluence_page(n, "2026-01-01T00:00:00.000Z") for n in range(200, 260)])
        cursor = "2026-02-01T00:00:00.000Z|0"
        first = list(poll_confluence(
            CONFLUENCE, PollRequest(page_size=100, page_limit=1, cursor=cursor),
            http=ConfluenceSite(rows)))
        checkpoint = first[-1].next_checkpoint
        self.assertIsNotNone(checkpoint)
        rest = list(poll_confluence(
            CONFLUENCE, PollRequest(page_size=100, cursor=cursor, checkpoint=checkpoint),
            http=ConfluenceSite(rows)))
        emitted = {d.external_id for p in first + rest for d in p.upserts}
        self.assertIn("1700", emitted, "a page updated after the cursor was dropped on resume")


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request: {request.url}")
        value = self.responses.pop(0)
        return value if isinstance(value, HttpResponse) else HttpResponse(200, {}, json.dumps(value).encode())


if __name__ == "__main__":
    unittest.main(verbosity=2)

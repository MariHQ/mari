from __future__ import annotations

import base64
import json
import unittest
import urllib.parse

from mari_components.connectors.confluence import (
    ConfluenceConfig,
    _site as confluence_site,
    poll_confluence,
    storage_to_text,
    validate_confluence,
)
from mari_components.connectors.google_drive import GoogleDriveConfig, poll_google_drive
from mari_components.connectors.google_drive import start_google_drive_watch
from mari_components.connectors.github import (
    GitHubConfig, list_github_repositories, poll_github, validate_github_team,
)
from mari_components.connectors.jira import JiraConfig, poll_jira
from mari_components.connectors.slack import (
    ARCHIVED_ONLY_MESSAGE, NO_CHANNELS_MESSAGE, SlackConfig, fetch_slack_thread_by_id,
    poll_slack, validate_slack,
)
from mari_components.errors import PermanentFailure
from mari_components.http import HttpResponse
from mari_components.types import PollRequest, SyncMode


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


class PriorityConnectorTests(unittest.TestCase):
    def test_github_team_validation_is_a_reusable_connector_operation(self):
        http = FakeHttp([{"slug": "docs"}])
        result = validate_github_team("token", "MariHQ", "docs", http=http)
        self.assertTrue(result.ok)
        self.assertEqual(result.identity, "MariHQ/docs")
        self.assertIn("/orgs/MariHQ/teams/docs", http.requests[0].url)

    def test_confluence_validation_and_ordered_checkpoint(self):
        config = ConfluenceConfig("https://example.atlassian.net", "me@example.com", "secret")
        validating = FakeHttp([{"results": []}])
        self.assertTrue(validate_confluence(config, http=validating).ok)
        auth = validating.requests[0].headers["Authorization"].removeprefix("Basic ")
        self.assertEqual(base64.b64decode(auth).decode(), "me@example.com:secret")
        polling = FakeHttp(
            [
                {
                    "size": 1,
                    "results": [
                        {
                            "id": "2",
                            "title": "Two",
                            "body": {"storage": {"value": "<h1>Hi</h1><p>Body</p>"}},
                            "version": {"number": 3},
                            "history": {"lastUpdated": {"when": "2026-01-02T00:00:00Z"}},
                        }
                    ],
                }
            ]
        )
        pages = list(poll_confluence(config, PollRequest(page_size=2), http=polling))
        self.assertTrue(pages[0].snapshot_complete)
        self.assertEqual(pages[0].upserts[0].body, "# Hi\nBody")
        self.assertEqual(pages[0].next_cursor, "2026-01-02T00:00:00Z|2")
        # Live Confluence sites reject "orderby" on this endpoint with a 400
        # regardless of value; never send it.
        self.assertNotIn("orderby", polling.requests[0].url)

    def test_confluence_author_prefers_last_editor_over_creator(self):
        config = ConfluenceConfig("https://example.atlassian.net", "me@example.com", "secret")
        polling = FakeHttp(
            [
                {
                    "size": 1,
                    "results": [
                        {
                            "id": "2",
                            "title": "Two",
                            "body": {"storage": {"value": "<p>Body</p>"}},
                            "version": {"number": 3, "when": "2026-01-02T00:00:00Z",
                                        "by": {"displayName": "Ana Ruiz"}},
                            "history": {"lastUpdated": {"when": "2026-01-02T00:00:00Z"},
                                        "createdBy": {"displayName": "Dev Park"}},
                        }
                    ],
                }
            ]
        )
        pages = list(poll_confluence(config, PollRequest(page_size=2), http=polling))
        self.assertEqual(pages[0].upserts[0].metadata["author"], "Ana Ruiz")

    def test_confluence_author_falls_back_to_creator_without_an_editor(self):
        config = ConfluenceConfig("https://example.atlassian.net", "me@example.com", "secret")
        polling = FakeHttp(
            [
                {
                    "size": 1,
                    "results": [
                        {
                            "id": "3",
                            "title": "Three",
                            "body": {"storage": {"value": "<p>Body</p>"}},
                            "version": {"number": 1, "when": "2026-01-01T00:00:00Z"},
                            "history": {"lastUpdated": {"when": "2026-01-01T00:00:00Z"},
                                        "createdBy": {"displayName": "Dev Park"}},
                        }
                    ],
                }
            ]
        )
        pages = list(poll_confluence(config, PollRequest(page_size=2), http=polling))
        self.assertEqual(pages[0].upserts[0].metadata["author"], "Dev Park")

    def test_confluence_author_is_empty_when_no_person_is_exposed(self):
        config = ConfluenceConfig("https://example.atlassian.net", "me@example.com", "secret")
        polling = FakeHttp(
            [
                {
                    "size": 1,
                    "results": [
                        {
                            "id": "4",
                            "title": "Four",
                            "body": {"storage": {"value": "<p>Body</p>"}},
                            "version": {"number": 1, "when": "2026-01-01T00:00:00Z"},
                            "history": {"lastUpdated": {"when": "2026-01-01T00:00:00Z"}},
                        }
                    ],
                }
            ]
        )
        pages = list(poll_confluence(config, PollRequest(page_size=2), http=polling))
        self.assertEqual(pages[0].upserts[0].metadata["author"], "")

    def test_confluence_site_strips_trailing_wiki_suffix(self):
        with_wiki = ConfluenceConfig("https://example.atlassian.net/wiki/", "me@example.com", "secret")
        self.assertEqual(confluence_site(with_wiki), "https://example.atlassian.net")
        bare = ConfluenceConfig("https://example.atlassian.net", "me@example.com", "secret")
        self.assertEqual(confluence_site(bare), "https://example.atlassian.net")

    def test_jira_search_jql_pages_by_next_page_token(self):
        http = FakeHttp(
            [
                {
                    "issues": [
                        {
                            "key": "MARI-1",
                            "fields": {
                                "summary": "Ship",
                                "description": {"content": [{"type": "paragraph", "content": [{"text": "Details"}]}]},
                                "status": {"name": "In Progress"},
                                "updated": "2026-01-01T00:00:00.000+0000",
                            },
                        }
                    ],
                    "nextPageToken": "page-2",
                    "isLast": False,
                },
                {
                    "issues": [
                        {
                            "key": "MARI-2",
                            "fields": {
                                "summary": "Ship more",
                                "description": {"content": []},
                                "status": {"name": "Done"},
                                "updated": "2026-01-02T00:00:00.000+0000",
                            },
                        }
                    ],
                    "isLast": True,
                },
            ]
        )
        config = JiraConfig("https://example.atlassian.net", "me@example.com", "secret", project_key="MARI")
        pages = list(poll_jira(config, PollRequest(page_size=1), http=http))
        self.assertEqual(len(pages), 2)
        self.assertFalse(pages[0].snapshot_complete)
        self.assertEqual(pages[0].next_checkpoint, "page-2")
        self.assertEqual(pages[0].upserts[0].external_id, "MARI-1")
        self.assertTrue(pages[1].snapshot_complete)
        self.assertEqual(pages[1].upserts[0].external_id, "MARI-2")

        first_url, second_url = http.requests[0].url, http.requests[1].url
        self.assertIn("/rest/api/3/search/jql", first_url)
        self.assertNotIn("startAt", first_url)
        self.assertNotIn("nextPageToken", first_url)
        self.assertIn("nextPageToken=page-2", second_url)
        jql_param = urllib.parse.parse_qs(urllib.parse.urlsplit(first_url).query)["jql"][0]
        # ORDER BY is a clause suffix, not a boolean condition: joining it
        # with "AND" is a JQL parse error on live Jira.
        self.assertNotIn("AND ORDER BY", jql_param)
        self.assertTrue(jql_param.endswith("ORDER BY updated ASC, key ASC"))
        fields_param = urllib.parse.parse_qs(urllib.parse.urlsplit(first_url).query)["fields"][0]
        self.assertEqual(
            fields_param,
            "summary,description,comment,status,updated,created,issuetype,assignee,reporter,labels",
        )
        self.assertEqual(pages[0].upserts[0].updated_at, "2026-01-01T00:00:00Z")
        self.assertEqual(pages[1].upserts[0].updated_at, "2026-01-02T00:00:00Z")

    def test_jira_falls_back_to_created_when_updated_is_missing(self):
        http = FakeHttp(
            [
                {
                    "issues": [
                        {
                            "key": "MARI-3",
                            "fields": {
                                "summary": "No updated field",
                                "description": {"content": []},
                                "status": {"name": "Backlog"},
                                "created": "2026-03-01T00:00:00.000+0000",
                            },
                        }
                    ],
                    "isLast": True,
                },
            ]
        )
        config = JiraConfig("https://example.atlassian.net", "me@example.com", "secret", project_key="MARI")
        pages = list(poll_jira(config, PollRequest(page_size=1), http=http))
        self.assertEqual(pages[0].upserts[0].updated_at, "2026-03-01T00:00:00Z")
        self.assertEqual(pages[0].upserts[0].revision, "2026-03-01T00:00:00.000+0000")

    def test_jira_author_prefers_assignee_over_reporter(self):
        http = FakeHttp(
            [
                {
                    "issues": [
                        {
                            "key": "MARI-4",
                            "fields": {
                                "summary": "Assigned issue",
                                "description": {"content": []},
                                "status": {"name": "In Progress"},
                                "updated": "2026-01-01T00:00:00.000+0000",
                                "assignee": {"displayName": "Mia Chen"},
                                "reporter": {"displayName": "Ana Ruiz"},
                            },
                        }
                    ],
                    "isLast": True,
                },
            ]
        )
        config = JiraConfig("https://example.atlassian.net", "me@example.com", "secret", project_key="MARI")
        pages = list(poll_jira(config, PollRequest(page_size=1), http=http))
        self.assertEqual(pages[0].upserts[0].metadata["author"], "Mia Chen")

    def test_jira_author_falls_back_to_reporter_when_unassigned(self):
        http = FakeHttp(
            [
                {
                    "issues": [
                        {
                            "key": "MARI-5",
                            "fields": {
                                "summary": "Unassigned issue",
                                "description": {"content": []},
                                "status": {"name": "Backlog"},
                                "updated": "2026-01-01T00:00:00.000+0000",
                                "reporter": {"displayName": "Ana Ruiz"},
                            },
                        }
                    ],
                    "isLast": True,
                },
            ]
        )
        config = JiraConfig("https://example.atlassian.net", "me@example.com", "secret", project_key="MARI")
        pages = list(poll_jira(config, PollRequest(page_size=1), http=http))
        self.assertEqual(pages[0].upserts[0].metadata["author"], "Ana Ruiz")

    def test_jira_author_is_empty_when_no_person_is_exposed(self):
        http = FakeHttp(
            [
                {
                    "issues": [
                        {
                            "key": "MARI-6",
                            "fields": {
                                "summary": "No one",
                                "description": {"content": []},
                                "status": {"name": "Backlog"},
                                "updated": "2026-01-01T00:00:00.000+0000",
                            },
                        }
                    ],
                    "isLast": True,
                },
            ]
        )
        config = JiraConfig("https://example.atlassian.net", "me@example.com", "secret", project_key="MARI")
        pages = list(poll_jira(config, PollRequest(page_size=1), http=http))
        self.assertEqual(pages[0].upserts[0].metadata["author"], "")

    def test_google_drive_snapshot_then_changes_tombstone(self):
        config = GoogleDriveConfig("token")
        initial = FakeHttp(
            [
                {"startPageToken": "start"},
                {
                    "files": [
                        {
                            "id": "d1",
                            "name": "Doc",
                            "mimeType": "application/vnd.google-apps.document",
                            "modifiedTime": "2026-01-01T00:00:00Z",
                            "permissions": [{"type": "group", "emailAddress": "Eng@Example.com"}],
                        }
                    ]
                },
                HttpResponse(200, {}, b"Document body"),
            ]
        )
        pages = list(poll_google_drive(config, PollRequest(mode=SyncMode.FULL), http=initial))
        self.assertEqual(pages[0].next_cursor, "changes:start")
        self.assertEqual(pages[0].upserts[0].acl.principals[0].identifier, "eng@example.com")
        changes = FakeHttp([{"changes": [{"fileId": "d1", "removed": True}], "newStartPageToken": "next"}])
        pages = list(
            poll_google_drive(config, PollRequest(cursor="changes:start"), http=changes)
        )
        self.assertEqual(pages[0].tombstones[0].external_id, "d1")
        self.assertEqual(pages[0].next_cursor, "changes:next")

    def test_storage_conversion(self):
        self.assertEqual(storage_to_text("<ul><li>One</li><li>Two</li></ul>"), "- One\n- Two")

    def test_github_repo_discovery_and_file_deletion(self):
        discovery = FakeHttp([[{"full_name": "MariHQ/mari"}]])
        self.assertEqual(list_github_repositories("token", http=discovery)[0]["full_name"], "MariHQ/mari")
        old_cursor = json.dumps({"head": "old", "item_since": "", "files": {"gone.md": "1"}})
        api = FakeHttp(
            [
                {"full_name": "MariHQ/mari", "default_branch": "main"},
                {"sha": "head", "commit": {"committer": {"date": "2026-08-19T18:42:07-07:00"}}},
            {"truncated": False, "tree": [
                {"type": "blob", "path": "README.md", "sha": "blob"},
                {"type": "blob", "path": "src/app.ts", "sha": "typescript"},
            ]},
                {"content": base64.b64encode(b"# Mari").decode()},
                [],
                [],
            ]
        )
        page = list(poll_github(GitHubConfig("token", "MariHQ/mari"), PollRequest(cursor=old_cursor), http=api))[0]
        self.assertTrue(page.snapshot_complete)
        self.assertEqual(page.upserts[0].external_id, "file:README.md")
        self.assertNotIn("src/app.ts", json.loads(page.next_cursor)["files"])
        self.assertEqual(page.upserts[0].updated_at, "2026-08-20T01:42:07Z")
        self.assertEqual(page.tombstones[0].external_id, "file:gone.md")

    def test_github_path_filters_are_connector_configuration(self):
        api = FakeHttp([
            {"full_name": "owner/repo", "default_branch": "main"},
            {"sha": "head", "commit": {"author": {"date": "2026-08-20T01:42:07Z"}}},
            {"truncated": False, "tree": [
                {"type": "blob", "path": "docs/guide.md", "sha": "one"},
                {"type": "blob", "path": "src/app.py", "sha": "two"},
            ]},
            {"content": base64.b64encode(b"Guide").decode()},
            [],
            [],
        ])
        page = list(poll_github(
            GitHubConfig("token", "owner/repo", paths=("docs/**",)),
            PollRequest(), http=api,
        ))[0]
        self.assertEqual([document.external_id for document in page.upserts],
                         ["file:docs/guide.md"])

    def test_slack_channel_root_is_restricted_and_incremental(self):
        api = FakeHttp(
            [
                {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
                {"ok": True, "channels": [{"id": "C1", "name": "product", "is_member": True}]},
                {"ok": True, "messages": [{"type": "message", "ts": "2.0", "user": "U1", "text": "Roadmap"}]},
            ]
        )
        page = list(poll_slack(SlackConfig("xoxb-token"), PollRequest(), http=api))[0]
        self.assertTrue(page.snapshot_complete)
        self.assertEqual(page.upserts[0].acl.principals[0].identifier, "C1")
        self.assertEqual(page.next_cursor, "2.000000")
        params = urllib.parse.parse_qs((api.requests[1].body or b"").decode())
        self.assertEqual(params["types"], ["public_channel,private_channel"])

    def test_slack_configured_private_channel_must_be_visible_to_the_bot(self):
        api = FakeHttp([
            {"ok": True, "members": []},
            {"ok": True, "channels": []},
        ])
        with self.assertRaisesRegex(
            PermanentFailure,
            "private-platform.*Invite the app to each channel.*groups:read.*groups:history",
        ):
            list(poll_slack(
                SlackConfig("xoxb-token", channels=("private-platform",)),
                PollRequest(), http=api,
            ))

    def test_slack_access_error_keeps_the_admins_own_spelling(self):
        api = FakeHttp([
            {"ok": True, "members": []},
            {"ok": True, "channels": []},
            {"ok": False, "error": "channel_not_found"},
        ])
        with self.assertRaisesRegex(PermanentFailure, "C0123ABCD"):
            list(poll_slack(
                SlackConfig("xoxb-token", channels=("C0123ABCD",)),
                PollRequest(), http=api,
            ))

    def test_slack_access_error_caps_the_channel_list_under_the_card_budget(self):
        api = FakeHttp([
            {"ok": True, "members": []},
            {"ok": True, "channels": []},
        ])
        channels = tuple(f"missing-channel-number-{index}" for index in range(9))
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(
                SlackConfig("xoxb-token", channels=channels),
                PollRequest(), http=api,
            ))
        message = str(caught.exception)
        self.assertIn("and 6 more", message)
        # the card truncates errors at 300 characters; the remediation must fit
        self.assertLess(len(message), 300)
        self.assertIn("groups:history (plus users:read).", message)
        self.assertIn("Check the name or ID.", message)

    def test_slack_access_error_for_unjoined_public_channels_fits_the_card_budget(self):
        channels = tuple(f"public-channel-number-{index}" for index in range(9))
        api = FakeHttp([
            {"ok": True, "members": []},
            {"ok": True, "channels": [
                {"id": f"C{index}", "name": name, "is_member": False}
                for index, name in enumerate(channels)
            ]},
        ])
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(
                SlackConfig("xoxb-token", channels=channels),
                PollRequest(), http=api,
            ))
        message = str(caught.exception)
        self.assertIn("and 6 more", message)
        self.assertLess(len(message), 300)
        self.assertIn("because the app is not a member. Invite the app to each channel.", message)
        # a listed channel is neither private nor misspelled: no scope or typo advice
        self.assertNotIn("groups:read", message)
        self.assertNotIn("Check the name", message)

    def test_slack_poll_refuses_a_token_that_is_in_no_channel(self):
        api = FakeHttp([
            {"ok": True, "members": []},
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": False}]},
        ])
        with self.assertRaisesRegex(PermanentFailure, "not a member of any Slack channel"):
            list(poll_slack(SlackConfig("xoxb-token"), PollRequest(), http=api))

    # ——— "Test connection" makes the same channel check the poll makes ———

    def test_slack_validate_fails_for_a_private_channel_the_bot_is_not_in(self):
        # Slack omits private channels the app is not a member of, so the list
        # comes back without it and the person must hear that before saving.
        api = FakeHttp([
            {"ok": True, "team": "Acme", "user": "mari"},
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True}]},
        ])
        result = validate_slack(SlackConfig("xoxb-token", channels=("#private-platform",)), http=api)
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, "permanent")
        self.assertRegex(
            result.message,
            r"^Slack could not access private-platform\. Invite the app to each channel\. "
            r"A private channel also needs groups:read and groups:history \(plus users:read\)\. "
            r"Still missing\? Check the name or ID\.$",
        )
        self.assertLess(len(result.message), 300)
        # the same words the poll uses, so the card and the connect dialog agree
        poll_api = FakeHttp([{"ok": True, "members": []}, {"ok": True, "channels": []}])
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(
                SlackConfig("xoxb-token", channels=("#private-platform",)), PollRequest(), http=poll_api,
            ))
        self.assertEqual(str(caught.exception), result.message)

    def test_slack_validate_fails_for_a_public_channel_the_bot_has_not_joined(self):
        api = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": False}]},
        ])
        result = validate_slack(SlackConfig("xoxb-token", channels=("general",)), http=api)
        self.assertFalse(result.ok)
        self.assertEqual(
            result.message,
            "Slack could not access general because the app is not a member. "
            "Invite the app to each channel.",
        )

    def test_slack_validate_names_the_channel_it_could_not_resolve(self):
        api = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [{"id": "C1", "name": "platform-team", "is_member": True}]},
            # an ID the listing lacks is asked about directly before it is declared missing
            {"ok": False, "error": "channel_not_found"},
        ])
        result = validate_slack(
            SlackConfig("xoxb-token", channels=("platform-team", "platfrom-tean", "C0123ABCD")), http=api,
        )
        self.assertFalse(result.ok)
        self.assertIn("C0123ABCD, platfrom-tean", result.message)
        self.assertNotIn("platform-team,", result.message)
        self.assertIn("Check the name or ID.", result.message)

    def test_slack_validate_passes_when_the_bot_is_a_member_and_stays_cheap(self):
        api = FakeHttp([
            {"ok": True, "team": "Acme", "user": "mari"},
            {"ok": True, "channels": [
                {"id": "C1", "name": "general", "is_member": True},
                {"id": "GPRIVATE1", "name": "private-platform", "is_member": True},
            ]},
        ])
        result = validate_slack(
            SlackConfig("xoxb-token", channels=("#Private-Platform", "GPRIVATE1")), http=api,
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.identity, "Acme")
        # auth.test plus one conversations.list pass, no users.list, no history
        self.assertEqual(
            [request.url.rsplit("/", 1)[-1] for request in api.requests],
            ["auth.test", "conversations.list"],
        )
        params = urllib.parse.parse_qs((api.requests[1].body or b"").decode())
        self.assertEqual(params["types"], ["public_channel,private_channel"])
        self.assertEqual(params["limit"], ["1000"])

    def test_slack_validate_refuses_a_token_that_is_in_no_channel(self):
        api = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": False}]},
        ])
        result = validate_slack(SlackConfig("xoxb-token"), http=api)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, NO_CHANNELS_MESSAGE)
        self.assertLess(len(result.message), 300)

    def test_slack_validate_names_the_scope_a_missing_scope_error_wants(self):
        api = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": False, "error": "missing_scope", "needed": "groups:read", "provided": "channels:read"},
        ])
        result = validate_slack(SlackConfig("xoxb-token", channels=("private-platform",)), http=api)
        self.assertFalse(result.ok)
        self.assertEqual(
            result.message,
            "Slack API error on conversations.list: missing_scope (the install needs groups:read)",
        )

    def test_slack_validate_still_reports_bad_credentials_first(self):
        api = FakeHttp([{"ok": False, "error": "invalid_auth"}])
        result = validate_slack(SlackConfig("xoxb-token", channels=("general",)), http=api)
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, "auth")
        self.assertIn("invalid_auth", result.message)

    def test_slack_channel_id_can_select_a_private_channel(self):
        api = FakeHttp([
            {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
            {"ok": True, "channels": [{
                "id": "GPRIVATE1", "name": "renamed-platform", "is_member": True,
            }]},
            {"ok": True, "messages": [{
                "type": "message", "ts": "2.0", "user": "U1", "text": "Private runbook",
            }]},
        ])
        page = list(poll_slack(
            SlackConfig("xoxb-token", channels=("GPRIVATE1",)),
            PollRequest(), http=api,
        ))[0]
        self.assertEqual([document.title for document in page.upserts], ["Private runbook"])

    # ——— membership is judged on what was actually scanned ———

    def test_slack_incomplete_listing_reports_an_unmatched_name_as_unchecked(self):
        # The walk hit its page cap with "far-away-channel" still unseen. It may
        # be fine, so "invite the app" would be a guess. Say what happened and
        # point at the ID, which conversations.info settles without a listing.
        page = {
            "ok": True,
            "channels": [{"id": "C1", "name": "general", "is_member": True}],
            "response_metadata": {"next_cursor": "more"},
        }
        api = FakeHttp([{"ok": True, "members": []}, page])
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(
                SlackConfig("xoxb-token", channels=("general", "far-away-channel")),
                PollRequest(page_limit=1), http=api,
            ))
        self.assertEqual(
            str(caught.exception),
            "This workspace has more channels than were scanned, so far-away-channel could not "
            "be checked. Configure each channel by its ID instead.",
        )
        self.assertNotIn("Invite the app", str(caught.exception))
        # validate walks five pages and then says the same thing
        validating = FakeHttp([{"ok": True, "team": "Acme"}] + [page] * 5)
        result = validate_slack(
            SlackConfig("xoxb-token", channels=("general", "far-away-channel")), http=validating,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.message, str(caught.exception))
        self.assertEqual(
            [request.url.rsplit("/", 1)[-1] for request in validating.requests].count("conversations.list"),
            5,
        )

    def test_slack_incomplete_listing_still_settles_a_configured_id_through_info(self):
        page = {
            "ok": True,
            "channels": [{"id": "C1", "name": "general", "is_member": True}],
            "response_metadata": {"next_cursor": "more"},
        }
        api = FakeHttp([
            {"ok": True, "members": []},
            page,
            {"ok": True, "channel": {"id": "C9FARAWAY", "name": "far-away", "is_member": False}},
        ])
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(
                SlackConfig("xoxb-token", channels=("general", "C9FARAWAY")),
                PollRequest(page_limit=1), http=api,
            ))
        # the ID was checked, so the answer is the definitive one, not "unchecked"
        self.assertEqual(
            str(caught.exception),
            "Slack could not access C9FARAWAY because the app is not a member. "
            "Invite the app to each channel.",
        )

    def test_slack_filtered_listing_stops_paging_once_every_wanted_channel_is_seen(self):
        api = FakeHttp([
            {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
            {
                "ok": True,
                "channels": [{"id": "C1", "name": "product", "is_member": True}],
                # Slack has more pages; nothing configured is on them
                "response_metadata": {"next_cursor": "more"},
            },
            {"ok": True, "messages": [{"type": "message", "ts": "2.0", "user": "U1", "text": "Roadmap"}]},
        ])
        page = list(poll_slack(
            SlackConfig("xoxb-token", channels=("product",)), PollRequest(page_limit=5), http=api,
        ))[0]
        self.assertEqual([document.title for document in page.upserts], ["Roadmap"])
        self.assertTrue(page.snapshot_complete)
        self.assertEqual(page.next_cursor, "2.000000")
        methods = [request.url.rsplit("/", 1)[-1] for request in api.requests]
        self.assertEqual(methods.count("conversations.list"), 1)
        # validate takes the same shortcut: auth.test, one list page, done
        validating = FakeHttp([{"ok": True, "team": "Acme"}, {
            "ok": True,
            "channels": [{"id": "C1", "name": "product", "is_member": True}],
            "response_metadata": {"next_cursor": "more"},
        }])
        self.assertTrue(validate_slack(SlackConfig("xoxb-token", channels=("#Product",)), http=validating).ok)
        self.assertEqual(len(validating.requests), 2)

    def test_slack_unfiltered_listing_walks_every_page(self):
        api = FakeHttp([
            {"ok": True, "team": "Acme"},
            {
                "ok": True,
                "channels": [{"id": "C1", "name": "product", "is_member": True}],
                "response_metadata": {"next_cursor": "more"},
            },
            {"ok": True, "channels": [{"id": "C2", "name": "design", "is_member": True}]},
        ])
        self.assertTrue(validate_slack(SlackConfig("xoxb-token"), http=api).ok)
        self.assertEqual(len(api.requests), 3)

    def test_slack_channel_id_missing_from_the_listing_is_resolved_with_info(self):
        # A large workspace can push a channel past the page cap, and the
        # listing never includes a private channel the app is not in. An ID
        # is answered by conversations.info either way.
        api = FakeHttp([
            {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True}]},
            {"ok": True, "channel": {
                "id": "GPRIVATE1", "name": "private-platform", "is_member": True, "is_private": True,
            }},
            {"ok": True, "messages": [{
                "type": "message", "ts": "2.0", "user": "U1", "text": "Private runbook",
            }]},
        ])
        page = list(poll_slack(
            SlackConfig("xoxb-token", channels=("GPRIVATE1",)), PollRequest(), http=api,
        ))[0]
        self.assertEqual([document.title for document in page.upserts], ["Private runbook"])
        self.assertEqual(page.upserts[0].metadata["channel_name"], "private-platform")
        methods = [request.url.rsplit("/", 1)[-1] for request in api.requests]
        self.assertEqual(methods, ["users.list", "conversations.list", "conversations.info", "conversations.history"])
        params = urllib.parse.parse_qs((api.requests[2].body or b"").decode())
        self.assertEqual(params["channel"], ["GPRIVATE1"])
        # a channel name is never mistaken for an ID, even one starting with c or g
        validating = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True}]},
        ])
        result = validate_slack(SlackConfig("xoxb-token", channels=("general", "growth")), http=validating)
        self.assertFalse(result.ok)
        self.assertEqual(len(validating.requests), 2)

    def test_slack_configured_archived_channel_gets_its_own_sentence(self):
        listing = {"ok": True, "channels": [
            {"id": "C1", "name": "launch-2024", "is_member": True, "is_archived": True},
        ]}
        api = FakeHttp([{"ok": True, "team": "Acme"}, listing])
        result = validate_slack(SlackConfig("xoxb-token", channels=("launch-2024",)), http=api)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "launch-2024 is archived. Remove it from the channel list.")
        params = urllib.parse.parse_qs((api.requests[1].body or b"").decode())
        self.assertEqual(params["exclude_archived"], ["false"])
        poll_api = FakeHttp([{"ok": True, "members": []}, listing])
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(SlackConfig("xoxb-token", channels=("launch-2024",)), PollRequest(), http=poll_api))
        self.assertEqual(str(caught.exception), result.message)
        # an archived channel reached by ID is reported the same way
        by_id = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": []},
            {"ok": True, "channel": {"id": "C1", "name": "launch-2024", "is_member": True, "is_archived": True}},
        ])
        self.assertEqual(
            validate_slack(SlackConfig("xoxb-token", channels=("C1",)), http=by_id).message,
            "C1 is archived. Remove it from the channel list.",
        )

    def test_slack_unfiltered_source_skips_archived_channels(self):
        api = FakeHttp([
            {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
            {"ok": True, "channels": [
                {"id": "C1", "name": "launch-2024", "is_member": True, "is_archived": True},
                {"id": "C2", "name": "product", "is_member": True},
            ]},
            {"ok": True, "messages": [{"type": "message", "ts": "2.0", "user": "U1", "text": "Roadmap"}]},
        ])
        page = list(poll_slack(SlackConfig("xoxb-token"), PollRequest(), http=api))[0]
        self.assertEqual([document.metadata["channel"] for document in page.upserts], ["C2"])
        # only archived channels left is not "not a member of any channel"
        archived_only = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [
                {"id": "C1", "name": "launch-2024", "is_member": True, "is_archived": True},
            ]},
        ])
        result = validate_slack(SlackConfig("xoxb-token"), http=archived_only)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, ARCHIVED_ONLY_MESSAGE)
        self.assertLess(len(result.message), 300)

    def test_slack_mixed_unjoined_and_unlisted_channels_get_two_sentences(self):
        listing = {"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": False}]}
        api = FakeHttp([{"ok": True, "team": "Acme"}, listing])
        result = validate_slack(
            SlackConfig("xoxb-token", channels=("general", "private-platform")), http=api,
        )
        self.assertFalse(result.ok)
        self.assertEqual(
            result.message,
            "Invite the app to general. private-platform is not listed. Check the name or ID. "
            "A private channel needs groups:read and groups:history.",
        )
        poll_api = FakeHttp([{"ok": True, "members": []}, listing])
        with self.assertRaises(PermanentFailure) as caught:
            list(poll_slack(
                SlackConfig("xoxb-token", channels=("general", "private-platform")),
                PollRequest(), http=poll_api,
            ))
        self.assertEqual(str(caught.exception), result.message)

    def test_slack_access_error_with_every_kind_of_problem_fits_the_card_budget(self):
        # worst case: three sets, each with many names at the thirty-character cap
        not_member = tuple(f"unjoined-public-channel-nr-{index:03d}" for index in range(1000))
        archived = tuple(f"archived-history-channels-{index:04d}" for index in range(1000))
        unlisted = tuple(f"private-or-misspelled-chan-{index:03d}" for index in range(1000))
        for name in (not_member[0], archived[0], unlisted[0]):
            self.assertGreaterEqual(len(name), 30)
        api = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [
                *({"id": f"C{index}", "name": name, "is_member": False}
                  for index, name in enumerate(not_member)),
                *({"id": f"A{index}", "name": name, "is_member": True, "is_archived": True}
                  for index, name in enumerate(archived)),
            ]},
        ])
        result = validate_slack(
            SlackConfig("xoxb-token", channels=not_member + archived + unlisted), http=api,
        )
        self.assertFalse(result.ok)
        self.assertLess(len(result.message), 300, result.message)
        self.assertIn("Invite the app to", result.message)
        self.assertIn("are archived. Remove them from the channel list.", result.message)
        self.assertIn("are not listed. Check the name or ID.", result.message)
        self.assertNotIn("\u2014", result.message)
        self.assertNotIn(";", result.message)
        # two sets at the same scale also fit
        two = FakeHttp([
            {"ok": True, "team": "Acme"},
            {"ok": True, "channels": [
                {"id": f"A{index}", "name": name, "is_member": True, "is_archived": True}
                for index, name in enumerate(archived)
            ]},
        ])
        message = validate_slack(SlackConfig("xoxb-token", channels=archived + unlisted), http=two).message
        self.assertLess(len(message), 300, message)
        self.assertIn("and 998 more are archived", message)
        # the unchecked message at scale
        capped = FakeHttp([
            {"ok": True, "team": "Acme"},
            *({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}} for _ in range(5)),
        ])
        message = validate_slack(SlackConfig("xoxb-token", channels=unlisted), http=capped).message
        self.assertLess(len(message), 300, message)
        self.assertIn("and 997 more could not be checked", message)

    def test_slack_polling_refetches_root_for_a_new_reply_row(self):
        api = FakeHttp([
            {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
            {"ok": True, "channels": [{"id": "C1", "name": "product", "is_member": True}]},
            {"ok": True, "messages": [{
                "type": "message", "ts": "3.0", "thread_ts": "1.0",
                "user": "U1", "text": "New reply",
            }]},
            {"ok": True, "messages": [
                {"type": "message", "ts": "1.0", "user": "U1", "text": "Question"},
                {"type": "message", "ts": "3.0", "thread_ts": "1.0", "user": "U1", "text": "New reply"},
            ]},
        ])
        page = list(poll_slack(
            SlackConfig("xoxb-token"), PollRequest(cursor="2.000000"), http=api,
        ))[0]
        self.assertEqual([document.external_id for document in page.upserts], ["thread:C1:1.0"])
        self.assertIn("New reply", page.upserts[0].body)
        self.assertEqual(page.next_cursor, "3.000000")

    def test_event_helpers_refetch_canonical_slack_thread_and_create_drive_watch(self):
        slack = FakeHttp([
            {"ok": True, "members": [{"id": "U1", "name": "Dana"}]},
            {"ok": True, "channel": {"id": "C1", "name": "private-test", "is_private": True}},
            {"ok": True, "messages": [
                {"type": "message", "ts": "1.0", "user": "U1", "text": "Question"},
                {"type": "message", "ts": "2.0", "thread_ts": "1.0", "user": "U1", "text": "Answer"},
            ]},
        ])
        document, complete = fetch_slack_thread_by_id(
            SlackConfig("xoxb-token", history_token="xoxp-token"), "C1", "1.0", http=slack,
        )
        self.assertTrue(complete)
        self.assertEqual(document.external_id, "thread:C1:1.0")
        self.assertEqual(document.metadata["channel_name"], "private-test")
        self.assertTrue(document.body.startswith("Slack channel: #private-test\n"))
        self.assertIn("Answer", document.body)

        drive = FakeHttp([{"id": "channel", "resourceId": "resource", "expiration": "123"}])
        watch = start_google_drive_watch(
            GoogleDriveConfig("token"), "page", "https://kb.example/webhooks/drive",
            "channel", "secret", http=drive, expiration_ms=100,
        )
        self.assertEqual(watch.resource_id, "resource")
        self.assertEqual(watch.expiration_ms, 123)
        request = drive.requests[0]
        self.assertNotIn("secret", request.url)
        self.assertEqual(json.loads(request.body)["token"], "secret")


if __name__ == "__main__":
    unittest.main()

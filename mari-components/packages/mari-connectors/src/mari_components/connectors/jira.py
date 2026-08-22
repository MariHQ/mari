"""Jira Cloud issue ingestion with bounded ordered JQL paging."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from typing import Any, Iterator
import urllib.parse

from mari_components.connectors._shared import json_response
from mari_components.connectors.protocol import ValidationResult
from mari_components.http import HttpRequest, HttpTransport
from mari_components.types import DocumentACL, KnowledgeDocument, PollPage, PollRequest


@dataclass(frozen=True, slots=True)
class JiraConfig:
    site_url: str
    email: str
    api_token: str
    project_key: str = ""
    jql: str = ""


def _headers(config: JiraConfig) -> dict[str, str]:
    if not config.site_url.strip() or not config.email.strip() or not config.api_token.strip():
        raise ValueError("Jira site URL, email, and API token are required")
    encoded = base64.b64encode(f"{config.email.strip()}:{config.api_token.strip()}".encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Accept": "application/json"}


def _site(config: JiraConfig) -> str:
    return config.site_url.strip().rstrip("/")


def validate_jira(config: JiraConfig, *, http: HttpTransport) -> ValidationResult:
    try:
        value = json_response(http, HttpRequest("GET", _site(config) + "/rest/api/3/myself", _headers(config)))
    except Exception as error:
        return ValidationResult(False, str(error))
    return ValidationResult(True, identity=str(value.get("emailAddress") or value.get("displayName") or ""))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, dict):
        own = str(value.get("text") or "")
        children = _text(value.get("content") or [])
        return own + children + ("\n" if value.get("type") in {"paragraph", "heading", "listItem"} else "")
    return ""


_FIELDS = "summary,description,comment,status,updated,issuetype,assignee,reporter,labels"


def poll_jira(config: JiraConfig, request: PollRequest, *, http: HttpTransport) -> Iterator[PollPage]:
    # /rest/api/3/search was removed by Atlassian; /search/jql pages by an
    # opaque nextPageToken instead of startAt/total, and rejects an unbounded
    # JQL (bare "ORDER BY updated" with no other clause), so a default query
    # always carries an explicit bound.
    page_token = request.checkpoint or None
    default_bound = (
        f"project = {config.project_key.strip()}" if config.project_key.strip() else "updated >= -365d"
    )
    where_parts = [config.jql.strip() or default_bound]
    if request.cursor:
        escaped = str(request.cursor).replace('"', '\\"')
        where_parts.append(f'updated > "{escaped}"')
    where_clause = " AND ".join(part for part in where_parts if part)
    # ORDER BY is a clause suffix, not a boolean condition: it must not be
    # AND-joined into the WHERE clause (that is a JQL parse error: "Expecting
    # a field name but got 'ORDER'").
    jql = f"{where_clause} ORDER BY updated ASC, key ASC"
    newest = str(request.cursor or "")
    for _ in range(request.page_limit):
        params: dict[str, Any] = {
            "jql": jql,
            "maxResults": request.page_size,
            "fields": _FIELDS,
        }
        if page_token:
            params["nextPageToken"] = page_token
        value = json_response(
            http,
            HttpRequest(
                "GET",
                _site(config) + "/rest/api/3/search/jql?" + urllib.parse.urlencode(params),
                _headers(config),
            ),
        )
        documents: list[KnowledgeDocument] = []
        issues = value.get("issues") or []
        for issue in issues:
            issue_fields = issue.get("fields") or {}
            key = str(issue.get("key") or issue.get("id") or "")
            comments = (issue_fields.get("comment") or {}).get("comments") or []
            body = _text(issue_fields.get("description"))
            for comment in comments:
                body += f"\n\nComment by {(comment.get('author') or {}).get('displayName', 'unknown')}:\n{_text(comment.get('body'))}"
            updated = str(issue_fields.get("updated") or "")
            newest = max(newest, updated)
            documents.append(
                KnowledgeDocument(
                    key,
                    f"{key}: {issue_fields.get('summary') or ''}",
                    body.strip(),
                    revision=updated,
                    updated_at=updated,
                    source_url=f"{_site(config)}/browse/{urllib.parse.quote(key, safe='')}",
                    acl=DocumentACL("connector_scope"),
                    metadata={"status": str((issue_fields.get("status") or {}).get("name") or "")},
                )
            )
        page_token = value.get("nextPageToken")
        is_last = value.get("isLast")
        if is_last is None:
            is_last = not page_token
        terminal = bool(is_last) or not issues
        yield PollPage(
            tuple(documents),
            next_cursor=newest if terminal else request.cursor,
            next_checkpoint=None if terminal else str(page_token or ""),
            snapshot_complete=terminal,
        )
        if terminal:
            return
    yield PollPage(
        next_cursor=request.cursor,
        next_checkpoint=str(page_token or ""),
        snapshot_complete=False,
        provider_metadata={"reason": "page_limit"},
    )

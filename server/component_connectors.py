"""Thin Mari host adapter for infrastructure-neutral connector components."""

from __future__ import annotations

import json
from typing import Callable, Iterator

from connectors import _net
from connectors._protocol import ACLMetadata, PollResult
from mari_components import PollRequest, SyncMode
from mari_components.connectors import (
    AirtableConfig, AsanaConfig, ConfluenceConfig, DropboxConfig, JiraConfig,
    LinearConfig, NotionConfig, SlackConfig, TrelloConfig, ZendeskConfig,
    poll_airtable, poll_asana, poll_confluence, poll_dropbox, poll_jira,
    poll_linear, poll_notion, poll_slack, poll_trello, poll_zendesk,
    validate_airtable, validate_asana, validate_confluence, validate_dropbox,
    validate_jira, validate_linear, validate_notion, validate_slack,
    validate_trello, validate_zendesk,
)
from mari_components.connectors.google_drive import (
    GoogleDriveConfig, GoogleOAuthRefresh, poll_google_drive,
    refresh_google_access_token, validate_google_drive,
)
from mari_components.http import HttpRequest, HttpResponse


_ENVELOPE = "mari-components:"


def _http(request: HttpRequest) -> HttpResponse:
    response = _net.fetch(
        request.url,
        method=request.method,
        headers=dict(request.headers),
        data=request.body,
        timeout=request.timeout,
    )
    return HttpResponse(response.status, dict(response.headers), response.body)


def _cursor(value: str | None) -> tuple[str | None, str | None]:
    if not value or not str(value).startswith(_ENVELOPE):
        return value, None
    try:
        payload = json.loads(str(value)[len(_ENVELOPE):])
        return payload.get("cursor"), payload.get("checkpoint")
    except (AttributeError, json.JSONDecodeError):
        raise ValueError("invalid connector checkpoint envelope") from None


def _envelope(cursor: str | None, checkpoint: str | None) -> str:
    return _ENVELOPE + json.dumps(
        {"cursor": cursor, "checkpoint": checkpoint}, sort_keys=True, separators=(",", ":"))


def _item(document) -> dict:
    return {
        "path": document.external_id,
        "title": document.title,
        "body": document.body,
        "updated_at": document.updated_at,
        "hash_hint": document.revision or None,
        "source_url": document.source_url or None,
        "acl": ACLMetadata(
            visibility=document.acl.visibility,
            principals=tuple(
                f"{principal.kind}:{principal.identifier}" for principal in document.acl.principals),
        ),
        **dict(document.metadata),
    }


def _collect(pages: Iterator, original_cursor: str | None) -> PollResult:
    items: list[dict] = []
    tombstones: list[str] = []
    last = None
    for page in pages:
        last = page
        items.extend(_item(document) for document in page.upserts)
        tombstones.extend(tombstone.external_id for tombstone in page.tombstones)
    if last is None:
        raise RuntimeError("connector returned no polling page")
    checkpoint = None
    cursor = last.next_cursor
    if not last.snapshot_complete:
        cursor = original_cursor
        checkpoint = _envelope(original_cursor, last.next_checkpoint)
    return PollResult(
        items,
        cursor,
        snapshot_complete=last.snapshot_complete,
        tombstones=sorted(set(tombstones)),
        checkpoint=checkpoint,
    )


def _request(cursor: str | None, cfg: dict) -> tuple[PollRequest, str | None]:
    base_cursor, checkpoint = _cursor(cursor)
    return PollRequest(
        mode=SyncMode.FULL if base_cursor is None else SyncMode.INCREMENTAL,
        cursor=base_cursor,
        checkpoint=checkpoint,
        page_size=max(1, min(int(cfg.get("page_size") or 100), 200)),
        page_limit=max(1, min(int(cfg.get("page_limit") or 20), 100)),
    ), base_cursor


def _gdrive(cfg: dict) -> GoogleDriveConfig:
    return GoogleDriveConfig(str(cfg.get("access_token") or ""), str(cfg.get("folder_id") or ""))


def _refresh_gdrive(cfg: dict) -> GoogleDriveConfig:
    refreshed = refresh_google_access_token(
        GoogleOAuthRefresh(
            str(cfg.get("refresh_token") or ""), str(cfg.get("client_id") or ""),
            str(cfg.get("client_secret") or "")),
        http=_http,
    )
    cfg["access_token"] = refreshed
    return _gdrive(cfg)


def _configs(key: str, cfg: dict):
    values = {
        "airtable": lambda: AirtableConfig(str(cfg.get("pat") or ""), str(cfg.get("base_id") or "")),
        "asana": lambda: AsanaConfig(str(cfg.get("pat") or ""), str(cfg.get("workspace") or ""), str(cfg.get("project_gid") or "")),
        "confluence": lambda: ConfluenceConfig(str(cfg.get("site_url") or ""), str(cfg.get("email") or ""), str(cfg.get("api_token") or ""), str(cfg.get("space_key") or "")),
        "dropbox": lambda: DropboxConfig(str(cfg.get("access_token") or ""), str(cfg.get("folder") or "")),
        "gdrive": lambda: _gdrive(cfg),
        "jira": lambda: JiraConfig(str(cfg.get("site_url") or ""), str(cfg.get("email") or ""), str(cfg.get("api_token") or ""), str(cfg.get("project_key") or ""), str(cfg.get("jql") or "")),
        "linear": lambda: LinearConfig(str(cfg.get("api_key") or ""), str(cfg.get("team_id") or "")),
        "notion": lambda: NotionConfig(str(cfg.get("token") or "")),
        "slack": lambda: SlackConfig(str(cfg.get("bot_token") or ""), tuple(value.strip() for value in str(cfg.get("channels") or "").split(",") if value.strip()), str(cfg.get("history_token") or "")),
        "trello": lambda: TrelloConfig(str(cfg.get("api_key") or ""), str(cfg.get("token") or "")),
        "zendesk": lambda: ZendeskConfig(str(cfg.get("subdomain") or ""), str(cfg.get("email") or ""), str(cfg.get("api_token") or "")),
    }
    return values[key]()


_VALIDATE = {
    "airtable": validate_airtable, "asana": validate_asana,
    "confluence": validate_confluence, "dropbox": validate_dropbox,
    "gdrive": validate_google_drive, "jira": validate_jira,
    "linear": validate_linear, "notion": validate_notion,
    "slack": validate_slack, "trello": validate_trello,
    "zendesk": validate_zendesk,
}
_POLL = {
    "airtable": poll_airtable, "asana": poll_asana,
    "confluence": poll_confluence, "dropbox": poll_dropbox,
    "gdrive": poll_google_drive, "jira": poll_jira,
    "linear": poll_linear, "notion": poll_notion,
    "slack": poll_slack, "trello": poll_trello,
    "zendesk": poll_zendesk,
}


def functions(key: str, legacy_validate: Callable, legacy_list: Callable) -> tuple[Callable, Callable]:
    """Return component-backed functions or the untouched excluded provider."""
    if key not in _POLL:
        return legacy_validate, legacy_list

    def validate(cfg: dict) -> str | None:
        try:
            result = _VALIDATE[key](_configs(key, cfg), http=_http)
            if not result.ok and key == "gdrive" and cfg.get("refresh_token"):
                result = _VALIDATE[key](_refresh_gdrive(cfg), http=_http)
            return None if result.ok else result.message
        except Exception as error:
            return str(error)

    def list_items(cfg: dict, cursor: str | None) -> PollResult:
        request, base_cursor = _request(cursor, cfg)
        try:
            pages = _POLL[key](_configs(key, cfg), request, http=_http)
            return _collect(pages, base_cursor)
        except Exception:
            if key != "gdrive" or not cfg.get("refresh_token"):
                raise
            pages = _POLL[key](_refresh_gdrive(cfg), request, http=_http)
            return _collect(pages, base_cursor)

    return validate, list_items

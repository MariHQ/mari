"""Mari connector — Linear (stdlib urllib, no deps).

Standalone-importable: no imports from server internals. All configuration
arrives via the `config` dict (api_key). GraphQL POSTs to api.linear.app.
The single raw HTTP call lives in `_http` so tests can monkeypatch it.
"""

from __future__ import annotations

import json

from . import _net
from ._protocol import ACLMetadata, PollResult

PROVIDER = {
    "key": "linear",
    "name": "Linear",
    "blurb": "Issues, specs, and discussion from your Linear workspace.",
    "fields": [
        {"key": "api_key", "label": "Personal API key", "secret": True,
         "placeholder": "lin_api_…",
         "help": "Linear → Settings → Security & access → Personal API keys"},
    ],
    "docs_url": "https://developers.linear.app/docs/graphql/working-with-the-graphql-api",
}

API = "https://api.linear.app/graphql"
PAGE_SIZE = 50
MAX_PAGES = 20

_ISSUES_QUERY = """
query Issues($after: String, $filter: IssueFilter) {
  issues(first: %d, after: $after, filter: $filter, orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier
      title
      description
      updatedAt
      project { name }
      team { name }
      comments(first: 20) {
        nodes { body createdAt user { name } }
      }
    }
  }
}
""" % PAGE_SIZE


class LinearError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _http(url: str, payload: bytes, headers: dict) -> tuple[int, bytes]:
    """The one raw HTTP call — tests monkeypatch this. Returns (status, body).

    Through the shared SSRF guard (AUTH-11) like every other connector: the
    endpoint is fixed here, but the guard is what keeps it that way."""
    try:
        resp = _net.fetch(url, method="POST", data=payload, headers=headers, timeout=30.0)
        return resp.status, resp.body
    except _net.Blocked as e:
        raise LinearError(f"Linear: refused to fetch {url} — {e}", 0) from None
    except _net.NetworkError as e:
        raise LinearError(f"Linear unreachable: {e}", 0) from None


def _graphql(config: dict, query: str, variables: dict | None = None) -> dict:
    key = (config.get("api_key") or "").strip()
    if not key:
        raise LinearError("Linear: API key is required", 400)
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    status, body = _http(API, payload, {
        "Authorization": key,  # personal API keys are sent bare, no Bearer
        "Content-Type": "application/json",
        "User-Agent": "mari-cloud-sync",
    })
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = {}
    if status in (400, 401) and _auth_error(data):
        raise LinearError("Linear: 401 Unauthorized (check the API key — "
                          "Settings → Security & access → Personal API keys)", 401)
    if status == 429:
        raise LinearError("Linear: rate limited (429) — retry later", 429)
    if status >= 400:
        msg = _first_error(data) or f"HTTP {status}"
        raise LinearError(f"Linear: {msg}", status)
    if data.get("errors"):
        if _auth_error(data):
            raise LinearError("Linear: 401 Unauthorized (check the API key)", 401)
        raise LinearError(f"Linear: GraphQL error — {_first_error(data)}", status)
    return data.get("data") or {}


def _first_error(data: dict) -> str:
    errs = data.get("errors") or []
    return str((errs[0] or {}).get("message", "")) if errs else ""


def _auth_error(data: dict) -> bool:
    msg = _first_error(data).lower()
    return "authentication" in msg or "not authenticated" in msg or "api key" in msg


# ————— contract surface —————


def validate(config: dict) -> str | None:
    """Cheap auth check: { viewer { id } }. None = ok, str = honest error."""
    try:
        data = _graphql(config, "query { viewer { id } }")
    except LinearError as e:
        return str(e)
    if not ((data.get("viewer") or {}).get("id")):
        return "Linear: unexpected response from the viewer query"
    return None


def _issue_to_item(node: dict) -> dict:
    ident = node.get("identifier") or ""
    parts: list[str] = []
    meta: list[str] = []
    team = (node.get("team") or {}).get("name")
    project = (node.get("project") or {}).get("name")
    if team:
        meta.append(f"Team: {team}")
    if project:
        meta.append(f"Project: {project}")
    if meta:
        parts.append(" · ".join(meta))
    desc = (node.get("description") or "").strip()
    if desc:
        parts.append(desc)
    comments = ((node.get("comments") or {}).get("nodes")) or []
    if comments:
        lines = ["## Comments"]
        for c in comments:
            author = ((c.get("user") or {}).get("name")) or "unknown"
            body = (c.get("body") or "").strip()
            if body:
                lines.append(f"- {author}: {body}")
        parts.append("\n".join(lines))
    return {
        "path": ident,
        "title": f"{ident}: {node.get('title') or ''}".rstrip(": "),
        "body": "\n\n".join(parts).strip(),
        "updated_at": node.get("updatedAt") or "",
        "hash_hint": node.get("updatedAt") or None,
    }


def list_items(config: dict, cursor: str | None) -> PollResult:
    """List issues ordered by updatedAt. Cursor = max updatedAt ISO timestamp
    seen; incremental runs filter server-side with updatedAt > cursor. Within
    one call, GraphQL pageInfo.endCursor pages through the result set."""
    variables: dict = {"after": None, "filter": None}
    if cursor:
        variables["filter"] = {"updatedAt": {"gt": cursor}}
    items: list[dict] = []
    max_seen = cursor or ""
    snapshot_complete = False
    end_cursor = None
    for _ in range(MAX_PAGES):
        data = _graphql(config, _ISSUES_QUERY, variables)
        conn = data.get("issues") or {}
        for node in conn.get("nodes") or []:
            item = _issue_to_item(node)
            if item["updated_at"] > max_seen:
                max_seen = item["updated_at"]
            items.append(item)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage") or not page.get("endCursor"):
            snapshot_complete = True
            break
        end_cursor = page["endCursor"]
        variables["after"] = end_cursor
    for item in items:
        item["acl"] = ACLMetadata(visibility="connector_scope")
    return PollResult(items, (max_seen or None) if snapshot_complete else cursor,
                      snapshot_complete=snapshot_complete)

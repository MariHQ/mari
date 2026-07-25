"""Mari Cloud connector — Notion pages import (stdlib urllib, no deps).

Internal-integration token. list_items walks POST /v1/search (pages, sorted by
last_edited_time descending) and stops at the cursor timestamp; per page the
block tree is fetched (2 levels deep, ≤300 blocks) and rendered to markdown-ish
text. Cursor = max last_edited_time seen (ISO). hash_hint = last_edited_time.

All HTTP goes through _api so tests can monkeypatch it.
"""

from __future__ import annotations

import json
import time

from . import _net

PROVIDER = {
    "key": "notion",
    "name": "Notion",
    "blurb": "Pages your team writes in — shared with your integration.",
    "fields": [
        {"key": "token", "label": "Internal integration token", "secret": True,
         "placeholder": "ntn_…",
         "help": "Notion → Settings → Integrations → your integration → "
                 "Internal Integration Secret. Share the pages with it."},
    ],
    "docs_url": "https://developers.notion.com/docs/create-a-notion-integration",
}

API = "https://api.notion.com"
NOTION_VERSION = "2022-06-28"
UA = "mari-cloud-sync"
MAX_BLOCKS = 300      # per page
MAX_DEPTH = 2         # children + grandchildren
MAX_PAGES_PER_SYNC = 200
CHILD_PAGE_SIZE = 100


class NotionError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    """One Notion API call. Retries once on 429 (Retry-After)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    for attempt in (0, 1):
        # Through the shared SSRF guard (AUTH-11), like every connector.
        try:
            resp = _net.fetch(f"{API}{path}", method=method, data=data,
                              headers=headers, timeout=30.0)
        except _net.Blocked as e:
            raise NotionError(f"Notion: refused to fetch {API}{path} — {e}", 0) from None
        except _net.NetworkError as e:
            raise NotionError(f"Notion unreachable: {e}", 0) from None
        if resp.status == 429 and attempt == 0:
            try:
                wait = min(float(_net.header(resp.headers, "Retry-After") or 1), 30.0)
            except ValueError:
                wait = 1.0
            time.sleep(wait)
            continue
        if resp.status >= 400:
            try:
                payload = json.loads(resp.body)
                msg = payload.get("message") or payload.get("code") or f"HTTP {resp.status}"
            except (json.JSONDecodeError, ValueError, AttributeError):
                msg = f"HTTP {resp.status}"
            raise NotionError(f"Notion API error on {path}: {msg}", resp.status)
        try:
            return json.loads(resp.body)
        except json.JSONDecodeError:
            raise NotionError(f"Notion: non-JSON response on {path}", resp.status) from None
    raise NotionError(f"Notion rate limited on {path} (retried once)", 429)


# ————— contract surface —————

def validate(config: dict) -> str | None:
    token = (config.get("token") or "").strip()
    if not token:
        return "token is required"
    try:
        me = _api("GET", "/v1/users/me", token)
    except NotionError as e:
        return str(e)
    if me.get("object") != "user":
        return "Notion returned an unexpected response for /v1/users/me"
    return None


# ————— block rendering —————

def _rich(rt: list | None) -> str:
    return "".join(seg.get("plain_text", "") for seg in (rt or []))


def _render_block(block: dict, depth: int) -> str | None:
    btype = block.get("type") or ""
    data = block.get(btype) or {}
    indent = "  " * max(0, depth - 1) if depth > 1 else ""
    if btype == "paragraph":
        text = _rich(data.get("rich_text"))
        return text if text else None
    if btype in ("heading_1", "heading_2", "heading_3"):
        text = _rich(data.get("rich_text"))
        return f"{'#' * int(btype[-1])} {text}" if text else None
    if btype in ("bulleted_list_item", "numbered_list_item"):
        return f"{indent}- {_rich(data.get('rich_text'))}"
    if btype == "to_do":
        box = "[x]" if data.get("checked") else "[ ]"
        return f"{indent}- {box} {_rich(data.get('rich_text'))}"
    if btype == "code":
        lang = data.get("language") or ""
        return f"```{lang}\n{_rich(data.get('rich_text'))}\n```"
    if btype == "quote":
        return f"> {_rich(data.get('rich_text'))}"
    if btype == "callout":
        return _rich(data.get("rich_text")) or None
    if btype == "toggle":
        return _rich(data.get("rich_text")) or None
    if btype == "child_page":
        return f"[page] {data.get('title', '')}"
    if btype == "divider":
        return "---"
    return None  # images, embeds, tables etc. — skipped


def _blocks_text(token: str, block_id: str, depth: int, budget: list[int]) -> list[str]:
    """Render children of block_id (recursing to MAX_DEPTH, ≤MAX_BLOCKS total)."""
    lines: list[str] = []
    start: str | None = None
    while budget[0] > 0:
        params = f"?page_size={CHILD_PAGE_SIZE}" + (f"&start_cursor={start}" if start else "")
        data = _api("GET", f"/v1/blocks/{block_id}/children{params}", token)
        for block in data.get("results", []):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            line = _render_block(block, depth)
            if line is not None:
                lines.append(line)
            if (block.get("has_children") and depth < MAX_DEPTH
                    and block.get("type") != "child_page" and budget[0] > 0):
                lines.extend(_blocks_text(token, block["id"], depth + 1, budget))
        start = data.get("next_cursor")
        if not data.get("has_more") or not start:
            break
    return lines


def _page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            title = _rich(prop.get("title"))
            if title:
                return title
    return "Untitled"


def list_items(config: dict, cursor: str | None) -> tuple[list[dict], str | None]:
    token = (config.get("token") or "").strip()
    if not token:
        raise NotionError("token is required")

    items: list[dict] = []
    max_edited = cursor or ""
    start: str | None = None
    done = False

    while not done and len(items) < MAX_PAGES_PER_SYNC:
        body: dict = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"timestamp": "last_edited_time", "direction": "descending"},
            "page_size": 100,
        }
        if start:
            body["start_cursor"] = start
        data = _api("POST", "/v1/search", token, body)
        for page in data.get("results", []):
            if page.get("object") != "page":
                continue
            edited = page.get("last_edited_time") or ""
            if cursor and edited and edited <= cursor:
                done = True  # sorted descending — everything after is older
                break
            budget = [MAX_BLOCKS]
            lines = _blocks_text(token, page["id"], 1, budget)
            items.append({
                "path": page["id"],
                "title": _page_title(page),
                "body": "\n\n".join(lines),
                "updated_at": edited,
                "hash_hint": edited or None,
            })
            if edited > max_edited:
                max_edited = edited
            if len(items) >= MAX_PAGES_PER_SYNC:
                break
        start = data.get("next_cursor")
        if not data.get("has_more") or not start:
            break

    return items, (max_edited or None)

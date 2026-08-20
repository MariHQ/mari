"""Mari connector — Confluence Cloud (stdlib urllib, no deps).

Standalone-importable: no imports from server internals. All configuration
arrives via the `config` dict (site_url, email, api_token, space_key).
The single raw HTTP call lives in `_http` so tests can monkeypatch it.
"""

from __future__ import annotations

import base64
import html
import json
import urllib.parse

from html.parser import HTMLParser

from . import _net
from ._protocol import ACLMetadata, PollResult

PROVIDER = {
    "key": "confluence",
    "name": "Confluence",
    "blurb": "Wiki pages and docs your team writes in Confluence spaces.",
    "fields": [
        {"key": "site_url", "label": "Site URL", "secret": False,
         "placeholder": "https://yourco.atlassian.net",
         "help": "Your Atlassian Cloud site, e.g. https://yourco.atlassian.net"},
        {"key": "email", "label": "Atlassian account email", "secret": False,
         "placeholder": "you@yourco.com",
         "help": "The email of the account the API token belongs to"},
        {"key": "api_token", "label": "API token", "secret": True,
         "placeholder": "ATATT…",
         "help": "id.atlassian.com → Security → Create and manage API tokens"},
        {"key": "space_key", "label": "Space key (optional)", "secret": False,
         "placeholder": "ENG",
         "help": "Limit sync to one space; leave blank for all readable spaces"},
        {"key": "webhook_secret", "label": "Webhook signing secret", "secret": True,
         "placeholder": "A long random secret",
         "help": "Use this secret to HMAC-sign Confluence webhook requests to Mari"},
    ],
    "docs_url": "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/",
}

PAGE_SIZE = 50
MAX_PAGES = 20  # per list_items call; cursor makes the next call cheap


class ConfluenceError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _site(config: dict) -> str:
    url = (config.get("site_url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _auth_header(config: dict) -> str:
    raw = f"{(config.get('email') or '').strip()}:{(config.get('api_token') or '').strip()}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _http(url: str, headers: dict) -> tuple[int, bytes]:
    """The one raw HTTP call — tests monkeypatch this. Returns (status, body).

    Goes through the shared SSRF guard: the site URL is user-supplied, so the
    scheme allowlist, the private-address check and the per-redirect re-check
    all apply (AUTH-11)."""
    try:
        resp = _net.fetch(url, headers=headers, timeout=30.0)
        return resp.status, resp.body
    except _net.Blocked as e:
        raise ConfluenceError(f"Confluence: refused to fetch {url} — {e}", 0) from None
    except _net.NetworkError as e:
        raise ConfluenceError(f"Confluence unreachable: {e}", 0) from None


def _get(config: dict, path: str, params: dict | None = None) -> dict:
    site = _site(config)
    if not site:
        raise ConfluenceError("Confluence: site URL is required", 400)
    url = site + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, body = _http(url, {
        "Authorization": _auth_header(config),
        "Accept": "application/json",
        "User-Agent": "mari-cloud-sync",
    })
    if status == 401:
        raise ConfluenceError(
            "Confluence: 401 Unauthorized (check email + API token)", 401)
    if status == 403:
        # Confluence Cloud answers 403 ("caller cannot access Confluence"),
        # not 401, when the email/API token pair is wrong.
        raise ConfluenceError(
            "Confluence: 403 Forbidden (check email + API token, and that the "
            "account can access this Confluence site)", 403)
    if status == 404:
        raise ConfluenceError(
            f"Confluence: 404 Not Found at {site} (check the site URL — expected "
            "https://yourco.atlassian.net)", 404)
    if status == 429:
        raise ConfluenceError("Confluence: rate limited (429) — retry later", 429)
    if status >= 400:
        raise ConfluenceError(f"Confluence: HTTP {status} on {path}", status)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ConfluenceError(
            f"Confluence: non-JSON response from {site} (is this a Confluence "
            "Cloud site?)", status) from None


# ————— storage XHTML → markdown-ish text —————


class _StorageText(HTMLParser):
    """Confluence body.storage XHTML → plain markdown-ish text.

    Keeps headings (#), list bullets, code fences; drops everything else.
    """

    _BLOCK_END = {"p", "div", "li", "ul", "ol", "table", "tr", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._list_depth = 0
        self._in_code = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("ul", "ol"):
            self._list_depth += 1
        elif tag == "li":
            self.out.append("\n" + "  " * max(self._list_depth - 1, 0) + "- ")
        elif tag in ("pre", "code") and not self._in_code:
            self._in_code += 1
            self.out.append("\n```\n" if tag == "pre" else "`")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "td" or tag == "th":
            self.out.append(" | ")
        elif tag == "ac:structured-macro":
            # code macros carry their body in CDATA inside ac:plain-text-body
            name = dict(attrs).get("ac:name", "")
            if name == "code":
                self._in_code += 1
                self.out.append("\n```\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n")
        elif tag in ("ul", "ol"):
            self._list_depth = max(self._list_depth - 1, 0)
        elif tag == "pre":
            self._in_code = max(self._in_code - 1, 0)
            self.out.append("\n```\n")
        elif tag == "code":
            if self._in_code:
                self._in_code = max(self._in_code - 1, 0)
                self.out.append("`")
        elif tag == "ac:structured-macro" and self._in_code:
            self._in_code = max(self._in_code - 1, 0)
            self.out.append("\n```\n")
        elif tag in self._BLOCK_END:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        self.out.append(data)

    def text(self) -> str:
        raw = "".join(self.out)
        lines = [ln.rstrip() for ln in raw.split("\n")]
        out: list[str] = []
        blank = 0
        for ln in lines:
            if not ln:
                blank += 1
                if blank > 1:
                    continue
            else:
                blank = 0
            out.append(ln)
        return "\n".join(out).strip()


def storage_to_text(xhtml: str) -> str:
    """Convert Confluence storage-format XHTML to markdown-ish plain text."""
    if not xhtml:
        return ""
    # CDATA (used by code macros) is invisible to HTMLParser — unwrap it first.
    xhtml = xhtml.replace("<![CDATA[", "").replace("]]>", "")
    p = _StorageText()
    try:
        p.feed(xhtml)
        p.close()
    except Exception:
        # last resort: crude tag strip
        import re
        return html.unescape(re.sub(r"<[^>]+>", " ", xhtml)).strip()
    return p.text()


# ————— contract surface —————


def validate(config: dict) -> str | None:
    """Cheap auth check: list one space. None = ok, str = honest error."""
    try:
        data = _get(config, "/wiki/rest/api/space", {"limit": 1})
    except ConfluenceError as e:
        return str(e)
    if not isinstance(data, dict) or "results" not in data:
        return "Confluence: unexpected response from the space API (check the site URL)"
    return None


def _page_to_item(page: dict) -> dict:
    body = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
    version = (page.get("version") or {}).get("number")
    updated = (((page.get("history") or {}).get("lastUpdated") or {}).get("when")) or ""
    if not updated:
        updated = (page.get("version") or {}).get("when") or ""
    return {
        "path": str(page.get("id")),
        "title": page.get("title") or f"Page {page.get('id')}",
        "body": storage_to_text(body),
        "updated_at": updated,
        "hash_hint": str(version) if version is not None else None,
    }


def fetch_page(config: dict, page_id: str) -> dict | None:
    """Fetch one page canonically; ``None`` is an authoritative tombstone.

    Webhook bodies are deliberately never indexed. They are only dirty hints;
    the event worker calls this endpoint so edits and deletes have the same
    trust boundary as polling.
    """
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ConfluenceError("Confluence: page id is required", 400)
    try:
        page = _get(
            config,
            f"/wiki/rest/api/content/{urllib.parse.quote(page_id, safe='')}",
            {"expand": "body.storage,version,history.lastUpdated,space"},
        )
    except ConfluenceError as exc:
        if exc.status == 404:
            return None
        raise
    if str(page.get("type") or "page") != "page":
        return None
    item = _page_to_item(page)
    item["acl"] = ACLMetadata(visibility="connector_scope")
    item["space_key"] = str((page.get("space") or {}).get("key") or "")
    return item


def list_items(config: dict, cursor: str | None) -> PollResult:
    """List pages (optionally one space). Cursor = max lastUpdated ISO timestamp
    seen; on incremental runs only pages with lastUpdated > cursor are returned
    (the content API can't server-filter by date, so we filter client-side)."""
    items: list[dict] = []
    start = 0
    raw_cursor = cursor or ""
    if raw_cursor.startswith("page:"):
        page_part, _, raw_cursor = raw_cursor.partition("|")
        try:
            start = max(0, int(page_part[5:]))
        except ValueError:
            start = 0
    cursor_time, _, cursor_id = raw_cursor.partition("|")
    max_seen = cursor_time
    last_key = (cursor_time, cursor_id)
    snapshot_complete = False
    for _ in range(MAX_PAGES):
        params = {
            "type": "page",
            "expand": "body.storage,version,history.lastUpdated",
            "limit": PAGE_SIZE,
            "start": start,
            "orderby": "history.lastUpdated asc",
        }
        space = (config.get("space_key") or "").strip()
        if space:
            params["spaceKey"] = space
        data = _get(config, "/wiki/rest/api/content", params)
        results = sorted(data.get("results") or [], key=lambda page: (
            _page_to_item(page)["updated_at"], str(page.get("id") or "")))
        for page in results:
            item = _page_to_item(page)
            item_key = (item["updated_at"], item["path"])
            if item["updated_at"] > max_seen:
                max_seen = item["updated_at"]
            if cursor and item_key <= (cursor_time, cursor_id):
                continue  # unchanged since last sync
            items.append(item)
            last_key = max(last_key, item_key)
        size = data.get("size", len(results))
        if not results or size < PAGE_SIZE:
            snapshot_complete = True
            break
        start += size
    for item in items:
        item["acl"] = ACLMetadata(visibility="connector_scope")
    checkpoint = (f"page:{start}|{last_key[0]}|{last_key[1]}"
                  if not snapshot_complete and last_key[0] else None)
    return PollResult(items, (max_seen or None) if snapshot_complete else cursor,
                      snapshot_complete=snapshot_complete,
                      checkpoint=checkpoint)

"""Mari connector — Zendesk Help Center (stdlib urllib, no deps).

Standalone-importable: no imports from server internals. All configuration
arrives via the `config` dict (subdomain, email, api_token). Syncs Help
Center articles only — the articles are the knowledge; tickets are skipped.
The single raw HTTP call lives in `_http` so tests can monkeypatch it.
"""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.parse

from html.parser import HTMLParser

from . import _net

PROVIDER = {
    "key": "zendesk",
    "name": "Zendesk",
    "blurb": "Help Center articles — your published support knowledge.",
    "fields": [
        {"key": "subdomain", "label": "Subdomain", "secret": False,
         "placeholder": "yourco",
         "help": "The yourco in yourco.zendesk.com"},
        {"key": "email", "label": "Agent email", "secret": False,
         "placeholder": "you@yourco.com",
         "help": "The email of the account the API token belongs to"},
        {"key": "api_token", "label": "API token", "secret": True,
         "placeholder": "",
         "help": "Admin Center → Apps and integrations → APIs → Zendesk API tokens"},
    ],
    "docs_url": "https://support.zendesk.com/hc/en-us/articles/4408889192858-Managing-access-to-the-Zendesk-API",
}

PER_PAGE = 100
MAX_PAGES = 20


class ZendeskError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _base(config: dict) -> str:
    sub = (config.get("subdomain") or "").strip()
    sub = sub.replace("https://", "").replace("http://", "")
    sub = sub.split(".zendesk.com")[0].strip("/ ")
    return f"https://{sub}.zendesk.com" if sub else ""


def _auth_header(config: dict) -> str:
    raw = (f"{(config.get('email') or '').strip()}/token:"
           f"{(config.get('api_token') or '').strip()}")
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _http(url: str, headers: dict) -> tuple[int, bytes]:
    """The one raw HTTP call — tests monkeypatch this. Returns (status, body).

    Goes through the shared SSRF guard: the subdomain is user-supplied, so the
    scheme allowlist, the private-address check and the per-redirect re-check
    all apply (AUTH-11)."""
    try:
        resp = _net.fetch(url, headers=headers, timeout=30.0)
        return resp.status, resp.body
    except _net.Blocked as e:
        raise ZendeskError(f"Zendesk: refused to fetch {url} — {e} "
                           "(check the subdomain)", 0) from None
    except _net.NetworkError as e:
        raise ZendeskError(f"Zendesk unreachable: {e} (check the subdomain)", 0) from None


def _get(config: dict, url: str) -> dict:
    status, body = _http(url, {
        "Authorization": _auth_header(config),
        "Accept": "application/json",
        "User-Agent": "mari-cloud-sync",
    })
    if status == 401:
        raise ZendeskError(
            "Zendesk: 401 Unauthorized (check email + API token, and that "
            "token access is enabled in Admin Center)", 401)
    if status == 403:
        raise ZendeskError("Zendesk: 403 Forbidden (account lacks API access)", 403)
    if status == 404:
        raise ZendeskError(
            "Zendesk: 404 Not Found (check the subdomain; Help Center must be "
            "enabled for article sync)", 404)
    if status == 429:
        raise ZendeskError("Zendesk: rate limited (429) — retry later", 429)
    if status >= 400:
        raise ZendeskError(f"Zendesk: HTTP {status}", status)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ZendeskError("Zendesk: non-JSON response (check the subdomain)",
                           status) from None


# ————— html → text —————


class _HtmlText(HTMLParser):
    _SKIP = {"script", "style"}
    _BLOCK = {"p", "div", "li", "ul", "ol", "table", "tr", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "pre":
            self.out.append("\n```\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(self._skip - 1, 0)
        elif tag == "pre":
            self.out.append("\n```\n")
        elif tag in self._BLOCK or tag.startswith("h"):
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)

    def text(self) -> str:
        raw = "".join(self.out)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def html_to_text(markup: str) -> str:
    if not markup:
        return ""
    p = _HtmlText()
    try:
        p.feed(markup)
        p.close()
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", markup)).strip()
    return p.text()


# ————— contract surface —————


def validate(config: dict) -> str | None:
    """Cheap auth check: /api/v2/users/me.json. None = ok, str = honest error.

    Zendesk answers 200 with a null-id anonymous user when credentials are
    silently ignored, so check the id too."""
    base = _base(config)
    if not base:
        return "Zendesk: subdomain is required"
    try:
        data = _get(config, base + "/api/v2/users/me.json")
    except ZendeskError as e:
        return str(e)
    user = (data or {}).get("user") or {}
    if not user.get("id"):
        return ("Zendesk: credentials not accepted (anonymous response from "
                "/users/me — check email + API token)")
    return None


def _article_to_item(a: dict) -> dict:
    updated = a.get("updated_at") or a.get("edited_at") or ""
    return {
        "path": f"article/{a.get('id')}",
        "title": a.get("title") or f"Article {a.get('id')}",
        "body": html_to_text(a.get("body") or ""),
        "updated_at": updated,
        "hash_hint": a.get("edited_at") or updated or None,
    }


def list_items(config: dict, cursor: str | None) -> tuple[list[dict], str | None]:
    """List Help Center articles sorted by updated_at ascending. Cursor = max
    updated_at ISO timestamp seen; incremental runs skip articles with
    updated_at <= cursor (filtered client-side; the list API has no since
    param). Pagination follows next_page URLs."""
    base = _base(config)
    if not base:
        raise ZendeskError("Zendesk: subdomain is required", 400)
    url = (base + "/api/v2/help_center/articles.json?" +
           urllib.parse.urlencode({
               "per_page": PER_PAGE,
               "sort_by": "updated_at",
               "sort_order": "asc",
           }))
    items: list[dict] = []
    max_seen = cursor or ""
    for _ in range(MAX_PAGES):
        data = _get(config, url)
        for a in data.get("articles") or []:
            item = _article_to_item(a)
            if item["updated_at"] > max_seen:
                max_seen = item["updated_at"]
            if cursor and item["updated_at"] and item["updated_at"] <= cursor:
                continue
            items.append(item)
        nxt = data.get("next_page")
        if not nxt:
            break
        url = nxt
    return items, (max_seen or None)

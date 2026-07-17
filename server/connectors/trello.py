"""Trello connector.

One Item per board: board name, its lists, and each list's cards (with
descriptions). Incremental: boards whose dateLastActivity <= cursor are
skipped; cursor advances to the newest dateLastActivity seen.

Standalone-importable; all raw HTTP goes through _http() (patchable in tests).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.trello.com/1"

PROVIDER = {
    "key": "trello",
    "name": "Trello",
    "blurb": "Boards, lists, and cards your team tracks work on.",
    "fields": [
        {
            "key": "api_key",
            "label": "API key",
            "secret": True,
            "placeholder": "32-char hex key",
            "help": "https://trello.com/power-ups/admin → your Power-Up → API key.",
        },
        {
            "key": "token",
            "label": "Token",
            "secret": True,
            "placeholder": "64-char token",
            "help": "From the same page: generate a Token authorized for your account (read scope).",
        },
    ],
    "docs_url": "https://developer.atlassian.com/cloud/trello/guides/rest-api/api-introduction/",
}


def _http(method, url, headers=None, body=None, timeout=30):
    """All raw HTTP for this connector. Returns (status, bytes)."""
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise ConnectionError(f"Trello: network error: {e.reason}")


def _url(config, path, **params):
    params["key"] = config.get("api_key", "")
    params["token"] = config.get("token", "")
    return f"{API}{path}?{urllib.parse.urlencode(params)}"


def _vendor_error(status, raw):
    return f"Trello API error (HTTP {status}): {raw.decode('utf-8', 'replace')[:300]}"


def _get_json(config, path, **params):
    status, raw = _http("GET", _url(config, path, **params))
    if status != 200:
        raise RuntimeError(_vendor_error(status, raw))
    return json.loads(raw.decode("utf-8"))


def validate(config):
    if not (config.get("api_key") or "").strip():
        return "API key is required."
    if not (config.get("token") or "").strip():
        return "Token is required."
    status, raw = _http("GET", _url(config, "/members/me", fields="id,username"))
    if status == 200:
        return None
    return _vendor_error(status, raw)


def _render_board(board, lists, cards):
    by_list = {}
    for c in cards:
        by_list.setdefault(c.get("idList"), []).append(c)
    lines = [f"# {board.get('name', 'Untitled board')}"]
    for lst in lists:
        lines += ["", f"## {lst.get('name', '')}"]
        for c in by_list.get(lst.get("id"), []):
            lines.append(f"- {c.get('name', '')}")
            desc = (c.get("desc") or "").strip()
            if desc:
                lines.extend(f"  {ln}" for ln in desc.splitlines())
    return "\n".join(lines).rstrip() + "\n"


def list_items(config, cursor):
    """cursor = ISO timestamp of the newest board dateLastActivity seen so far."""
    boards = _get_json(config, "/members/me/boards",
                       fields="name,dateLastActivity,closed", filter="open")
    items, newest = [], cursor
    for b in boards:
        last = b.get("dateLastActivity") or ""
        if cursor and last and last <= cursor:
            continue
        bid = b["id"]
        lists = _get_json(config, f"/boards/{bid}/lists", fields="name")
        cards = _get_json(config, f"/boards/{bid}/cards", fields="name,desc,idList")
        if newest is None or last > newest:
            newest = last
        items.append({
            "path": bid,
            "title": b.get("name", bid),
            "body": _render_board(b, lists, cards),
            "updated_at": last,
            "hash_hint": last or None,
        })
    return items, newest

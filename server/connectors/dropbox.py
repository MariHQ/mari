"""Dropbox connector.

Markdown/text/Paper files from a folder (recursive). Incremental sync uses
Dropbox's native list_folder cursor via /2/files/list_folder/continue — an
exact fit for the framework's cursor contract. Files over 1 MB are skipped.

Standalone-importable; all raw HTTP goes through _http() (patchable in tests).
"""

import json

from . import _net
from ._protocol import ACLMetadata, PollResult

API = "https://api.dropboxapi.com/2"
CONTENT_API = "https://content.dropboxapi.com/2"

PROVIDER = {
    "key": "dropbox",
    "name": "Dropbox",
    "blurb": "Markdown, text, and Paper docs from a Dropbox folder.",
    "fields": [
        {
            "key": "access_token",
            "label": "Access token",
            "secret": True,
            "placeholder": "sl.…",
            "help": "Dropbox App Console → your app → Generate access token (scopes files.metadata.read, files.content.read).",
        },
        {
            "key": "folder",
            "label": "Folder path (optional)",
            "secret": False,
            "placeholder": "/notes",
            "help": "Path like /team/docs. Blank = entire Dropbox.",
        },
    ],
    "docs_url": "https://www.dropbox.com/developers/documentation/http/documentation",
}

_EXTS = (".md", ".txt", ".markdown", ".paper")
_MAX_BYTES = 1024 * 1024
MAX_PAGES = 50


def _http(method, url, headers=None, body=None, timeout=60):
    """All raw HTTP for this connector. Returns (status, bytes).

    Routed through the shared SSRF guard (AUTH-11): http/https only, no
    private/loopback/link-local target, every redirect hop re-checked, and the
    Authorization header dropped if a redirect crosses to another origin."""
    try:
        resp = _net.fetch(url, method=method, headers=headers or {}, data=body,
                          timeout=timeout)
        return resp.status, resp.body
    except _net.Blocked as e:
        raise ConnectionError(f"Dropbox: refused to fetch {url} — {e}") from None
    except _net.NetworkError as e:
        raise ConnectionError(f"Dropbox: network error: {e}") from None


def _vendor_error(status, raw):
    text = raw.decode("utf-8", "replace")
    try:
        text = json.loads(text).get("error_summary", text)
    except Exception:
        pass
    return f"Dropbox API error (HTTP {status}): {text[:300]}"


def _rpc(config, path, payload):
    headers = {
        "Authorization": f"Bearer {config.get('access_token', '')}",
        "Content-Type": "application/json",
    }
    body = json.dumps(payload).encode("utf-8") if payload is not None else b"null"
    status, raw = _http("POST", f"{API}{path}", headers=headers, body=body)
    if status != 200:
        raise RuntimeError(_vendor_error(status, raw))
    return json.loads(raw.decode("utf-8"))


def validate(config):
    if not (config.get("access_token") or "").strip():
        return "Access token is required."
    headers = {"Authorization": f"Bearer {config.get('access_token', '')}"}
    status, raw = _http("POST", f"{API}/users/get_current_account", headers=headers)
    if status == 200:
        return None
    return _vendor_error(status, raw)


def _download(config, path_lower):
    headers = {
        "Authorization": f"Bearer {config.get('access_token', '')}",
        "Dropbox-API-Arg": json.dumps({"path": path_lower}),
    }
    status, raw = _http("POST", f"{CONTENT_API}/files/download", headers=headers)
    if status != 200:
        raise RuntimeError(_vendor_error(status, raw))
    return raw.decode("utf-8", "replace")


def _wanted(entry):
    return (
        entry.get(".tag") == "file"
        and entry.get("name", "").lower().endswith(_EXTS)
        and entry.get("size", 0) <= _MAX_BYTES
    )


def list_items(config, cursor):
    """cursor = Dropbox's native list_folder cursor (delta feed)."""
    if cursor:
        data = _rpc(config, "/files/list_folder/continue", {"cursor": cursor})
    else:
        folder = (config.get("folder") or "").strip()
        data = _rpc(config, "/files/list_folder", {
            "path": folder if folder else "",
            "recursive": True,
        })

    entries = list(data.get("entries", []))
    pages = 1
    while data.get("has_more") and pages < MAX_PAGES:
        data = _rpc(config, "/files/list_folder/continue", {"cursor": data["cursor"]})
        entries.extend(data.get("entries", []))
        pages += 1
    next_cursor = data.get("cursor")
    complete = not bool(data.get("has_more"))

    items = []
    tombstones = []
    for e in entries:
        if e.get(".tag") == "deleted":
            path = e.get("path_lower") or ""
            if path:
                tombstones.append(path.lstrip("/"))
            continue
        if not _wanted(e):
            continue  # skips folders, non-text, oversized
        path = e.get("path_lower") or e["id"]
        items.append({
            "path": path.lstrip("/"),
            "title": e.get("name", path),
            "body": _download(config, path),
            "updated_at": e.get("server_modified", ""),
            "hash_hint": e.get("content_hash") or e.get("rev") or None,
            "acl": ACLMetadata(visibility="connector_scope"),
        })
    return PollResult(items, next_cursor if complete else cursor,
                      snapshot_complete=complete, tombstones=tombstones,
                      checkpoint=None if complete else next_cursor)

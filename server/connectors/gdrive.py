"""Google Drive connector.

Auth approach (honest limitation): stdlib-only Python cannot sign RS256 JWTs,
which the service-account flow requires, and `cryptography` is not a project
dependency. So this connector takes a user-supplied OAuth2 access token
(e.g. from https://developers.google.com/oauthplayground with the
drive.readonly scope). Access tokens are short-lived (~1 hour); the blurb
and field help say so plainly.

Standalone-importable; all raw HTTP goes through _http() (patchable in tests).
"""

import json
import urllib.parse

from . import _net
from ._protocol import ACLMetadata, PollResult

API = "https://www.googleapis.com/drive/v3"

PROVIDER = {
    "key": "gdrive",
    "name": "Google Drive",
    "blurb": (
        "Docs and text files from a Drive folder. Uses a short-lived OAuth "
        "access token (~1h) — service-account keys need RSA signing this "
        "server doesn't ship; re-connect with a fresh token when it expires."
    ),
    "fields": [
        {
            "key": "access_token",
            "label": "OAuth2 access token",
            "secret": True,
            "placeholder": "ya29.…",
            "help": (
                "Short-lived token with the drive.readonly scope. Easiest: "
                "https://developers.google.com/oauthplayground → Drive API v3 "
                "→ authorize → Exchange for tokens. Expires in ~1 hour."
            ),
        },
        {
            "key": "folder_id",
            "label": "Folder ID (optional)",
            "secret": False,
            "placeholder": "1AbC…",
            "help": "Limit sync to one folder (the ID from the folder URL). Blank = whole Drive.",
        },
    ],
    "docs_url": "https://developers.google.com/drive/api/guides/about-sdk",
}

_DOC_MIME = "application/vnd.google-apps.document"
_TEXT_MIMES = ("text/plain", "text/markdown")
_PAGE_SIZE = 100


def _http(method, url, headers=None, body=None, timeout=30):
    """All raw HTTP for this connector. Returns (status, bytes).

    Routed through the shared SSRF guard (AUTH-11): http/https only, no
    private/loopback/link-local target, every redirect hop re-checked, and the
    Authorization header dropped if a redirect crosses to another origin."""
    try:
        resp = _net.fetch(url, method=method, headers=headers or {}, data=body,
                          timeout=timeout)
        return resp.status, resp.body
    except _net.Blocked as e:
        raise ConnectionError(f"Google Drive: refused to fetch {url} — {e}") from None
    except _net.NetworkError as e:
        raise ConnectionError(f"Google Drive: network error: {e}") from None


def _headers(config):
    return {"Authorization": f"Bearer {config.get('access_token', '')}"}


def _q_literal(value):
    """Escape a value for a Drive `q=` string literal (SQL-2).

    Drive's search grammar quotes literals with ' and escapes with a backslash.
    Unescaped, a folder_id containing ' closes the literal and the rest of the
    field becomes query syntax — dropping `trashed = false` and the MIME filter,
    so the sync pulls files the user never scoped it to. Backslash first, then
    the quote, or the escape itself gets escaped twice."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _vendor_error(status, raw):
    try:
        msg = json.loads(raw.decode("utf-8", "replace"))["error"]["message"]
    except Exception:
        msg = raw.decode("utf-8", "replace")[:300]
    return f"Google Drive API error (HTTP {status}): {msg}"


def validate(config):
    if not (config.get("access_token") or "").strip():
        return "Access token is required."
    status, raw = _http("GET", f"{API}/about?fields=user", headers=_headers(config))
    if status == 200:
        return None
    return _vendor_error(status, raw)


def _fetch_body(config, f):
    fid, mime = urllib.parse.quote(str(f["id"]), safe=""), f.get("mimeType", "")
    if mime == _DOC_MIME:
        url = f"{API}/files/{fid}/export?mimeType=text%2Fplain"
    else:
        url = f"{API}/files/{fid}?alt=media"
    status, raw = _http("GET", url, headers=_headers(config))
    if status != 200:
        raise RuntimeError(_vendor_error(status, raw))
    return raw.decode("utf-8", "replace")


def list_items(config, cursor):
    """cursor = ISO timestamp of the newest modifiedTime seen so far."""
    q_parts = [
        "trashed = false",
        f"(mimeType = '{_DOC_MIME}' or mimeType = '{_TEXT_MIMES[0]}' or mimeType = '{_TEXT_MIMES[1]}')",
    ]
    folder = (config.get("folder_id") or "").strip()
    if folder:
        q_parts.append(f"'{_q_literal(folder)}' in parents")
    if cursor:
        q_parts.append(f"modifiedTime > '{_q_literal(cursor)}'")
    q = " and ".join(q_parts)

    files, page_token = [], None
    while True:
        params = {
            "q": q,
            "pageSize": str(_PAGE_SIZE),
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum)",
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"{API}/files?{urllib.parse.urlencode(params)}"
        status, raw = _http("GET", url, headers=_headers(config))
        if status != 200:
            raise RuntimeError(_vendor_error(status, raw))
        data = json.loads(raw.decode("utf-8"))
        files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    items, newest = [], cursor
    for f in files:
        mod = f.get("modifiedTime", "")
        if newest is None or mod > newest:
            newest = mod
        items.append({
            "path": f["id"],
            "title": f.get("name", f["id"]),
            "body": _fetch_body(config, f),
            "updated_at": mod,
            "hash_hint": f.get("md5Checksum") or mod or None,
            "acl": ACLMetadata(visibility="connector_scope"),
        })
    return PollResult(items, newest, snapshot_complete=True)

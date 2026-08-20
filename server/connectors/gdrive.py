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
from ._protocol import ACLMetadata, FullResyncRequired, PollResult

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
        {"key": "refresh_token", "label": "OAuth2 refresh token (optional)", "secret": True,
         "placeholder": "1//…", "help": "Keeps polling after the access token expires."},
        {"key": "client_id", "label": "OAuth client ID (for refresh)", "secret": False,
         "placeholder": "…apps.googleusercontent.com"},
        {"key": "client_secret", "label": "OAuth client secret (for refresh)", "secret": True,
         "placeholder": "GOCSPX-…"},
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
_MAX_PAGES = 20
_TOKEN_API = "https://oauth2.googleapis.com/token"


class DrivePageTokenExpired(FullResyncRequired):
    """The Changes cursor can no longer be used and requires reconciliation."""


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


def _refresh(config):
    if not all((config.get("refresh_token"), config.get("client_id"), config.get("client_secret"))):
        return False
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": config["refresh_token"],
                                  "client_id": config["client_id"], "client_secret": config["client_secret"]}).encode()
    status, raw = _http("POST", _TOKEN_API, headers={"Content-Type": "application/x-www-form-urlencoded"}, body=body)
    try:
        token = json.loads(raw).get("access_token") if status == 200 else None
    except json.JSONDecodeError:
        token = None
    if token:
        config["access_token"] = token
        return True
    return False


def _headers(config):
    if not (config.get("access_token") or "").strip():
        _refresh(config)
    return {"Authorization": f"Bearer {config.get('access_token', '')}"}


def _request(config, method, url, body=None, extra_headers=None):
    headers = {**_headers(config), **(extra_headers or {})}
    status, raw = _http(method, url, headers=headers, body=body)
    if status == 401 and config.get("refresh_token") and _refresh(config):
        headers = {**_headers(config), **(extra_headers or {})}
        status, raw = _http(method, url, headers=headers, body=body)
    return status, raw


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
    if not (config.get("access_token") or "").strip() and not config.get("refresh_token"):
        return "Access token or refresh-token credentials are required."
    status, raw = _request(config, "GET", f"{API}/about?fields=user")
    if status == 200:
        return None
    return _vendor_error(status, raw)


def _fetch_body(config, f):
    fid, mime = urllib.parse.quote(str(f["id"]), safe=""), f.get("mimeType", "")
    if mime == _DOC_MIME:
        url = f"{API}/files/{fid}/export?mimeType=text%2Fplain"
    else:
        url = f"{API}/files/{fid}?alt=media"
    status, raw = _request(config, "GET", url)
    if status != 200:
        raise RuntimeError(_vendor_error(status, raw))
    return raw.decode("utf-8", "replace")


def _acl(file: dict) -> ACLMetadata:
    principals: set[str] = set()
    public = False
    for permission in file.get("permissions") or []:
        if permission.get("deleted"):
            continue
        kind = str(permission.get("type") or "")
        if kind == "anyone":
            public = True
        elif kind in {"user", "group"} and permission.get("emailAddress"):
            principals.add(f"{kind}:{str(permission['emailAddress']).lower()}")
        elif kind == "domain" and permission.get("domain"):
            principals.add(f"domain:{str(permission['domain']).lower()}")
    return ACLMetadata(visibility="public" if public else "restricted",
                       principals=tuple(sorted(principals)))


def list_changes(config: dict, page_token: str, *, max_pages: int = _MAX_PAGES) -> PollResult:
    """Drain a bounded portion of Drive Changes, preserving the next token."""
    if not page_token:
        raise ValueError("Google Drive changes page token is required")
    items: list[dict] = []
    tombstones: list[str] = []
    next_token = page_token
    folder = str(config.get("folder_id") or "").strip()
    for _ in range(max(1, max_pages)):
        params = {
            "pageToken": next_token, "pageSize": str(_PAGE_SIZE),
            "includeRemoved": "true", "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,modifiedTime,md5Checksum,trashed,parents,permissions(type,emailAddress,domain,allowFileDiscovery,deleted)))",
        }
        status, raw = _request(config, "GET", f"{API}/changes?{urllib.parse.urlencode(params)}")
        if status == 410:
            raise DrivePageTokenExpired("Google Drive changes page token expired (HTTP 410)")
        if status != 200:
            raise RuntimeError(_vendor_error(status, raw))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Google Drive changes returned invalid JSON") from exc
        for change in data.get("changes") or []:
            file = change.get("file") or {}
            file_id = str(change.get("fileId") or file.get("id") or "")
            if not file_id:
                continue
            in_scope = not folder or folder in (file.get("parents") or [])
            if change.get("removed") or file.get("trashed") or not in_scope:
                tombstones.append(file_id)
                continue
            if file.get("mimeType") not in (_DOC_MIME, *_TEXT_MIMES):
                continue
            items.append({
                "path": file_id, "title": file.get("name", file_id),
                "body": _fetch_body(config, file),
                "updated_at": file.get("modifiedTime", ""),
                "hash_hint": file.get("md5Checksum") or file.get("modifiedTime") or None,
                "acl": _acl(file),
            })
        if data.get("nextPageToken"):
            next_token = str(data["nextPageToken"])
            continue
        new_token = str(data.get("newStartPageToken") or "")
        if not new_token:
            raise RuntimeError("Google Drive changes ended without newStartPageToken")
        return PollResult(items, f"changes:{new_token}", snapshot_complete=True,
                          tombstones=tombstones)
    checkpoint = f"changes:{next_token}"
    return PollResult(items, f"changes:{page_token}", snapshot_complete=False,
                      tombstones=tombstones, checkpoint=checkpoint)


def list_items(config, cursor):
    """cursor = ISO timestamp of the newest modifiedTime seen so far."""
    if cursor and str(cursor).startswith("changes:"):
        return list_changes(config, str(cursor)[8:])

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

    start_token = None
    try:
        status, raw = _request(config, "GET", f"{API}/changes/startPageToken")
        start_token = json.loads(raw).get("startPageToken") if status == 200 else None
    except (json.JSONDecodeError, AttributeError):
        pass
    files, page_token, complete = [], None, False
    for _ in range(_MAX_PAGES):
        params = {
            "q": q,
            "pageSize": str(_PAGE_SIZE),
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum,parents,permissions(type,emailAddress,domain,allowFileDiscovery,deleted))",
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"{API}/files?{urllib.parse.urlencode(params)}"
        status, raw = _request(config, "GET", url)
        if status != 200:
            raise RuntimeError(_vendor_error(status, raw))
        data = json.loads(raw.decode("utf-8"))
        files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            complete = True
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
            "acl": _acl(f),
        })
    new_cursor = f"changes:{start_token}" if complete and start_token else newest
    return PollResult(items, new_cursor if complete else cursor, snapshot_complete=complete)

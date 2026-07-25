"""Mari Cloud connector — Jira Cloud (stdlib urllib, no deps).

Standalone-importable: no imports from server internals. All configuration
arrives via the `config` dict (site_url, email, api_token, jql).
The single raw HTTP call lives in `_http` so tests can monkeypatch it.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse

from . import _net

PROVIDER = {
    "key": "jira",
    "name": "Jira",
    "blurb": "Issues, decisions, and discussion from your Jira projects.",
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
        {"key": "jql", "label": "JQL filter (optional)", "secret": False,
         "placeholder": "updated >= -30d ORDER BY updated DESC",
         "help": "Which issues to sync; defaults to everything updated in 30 days"},
    ],
    "docs_url": "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/",
}

DEFAULT_JQL = "updated >= -30d ORDER BY updated DESC"
PAGE_SIZE = 50
MAX_PAGES = 20


class JiraError(Exception):
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
        raise JiraError(f"Jira: refused to fetch {url} — {e}", 0) from None
    except _net.NetworkError as e:
        raise JiraError(f"Jira unreachable: {e}", 0) from None


def _get(config: dict, path: str, params: dict | None = None) -> dict:
    site = _site(config)
    if not site:
        raise JiraError("Jira: site URL is required", 400)
    url = site + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, body = _http(url, {
        "Authorization": _auth_header(config),
        "Accept": "application/json",
        "User-Agent": "mari-cloud-sync",
    })
    if status == 401:
        raise JiraError("Jira: 401 Unauthorized (check email + API token)", 401)
    if status == 403:
        raise JiraError("Jira: 403 Forbidden (token lacks access to this site)", 403)
    if status == 404:
        raise JiraError(
            f"Jira: 404 Not Found at {site} (check the site URL — expected "
            "https://yourco.atlassian.net)", 404)
    if status == 429:
        raise JiraError("Jira: rate limited (429) — retry later", 429)
    if status >= 400:
        detail = ""
        try:
            msgs = json.loads(body).get("errorMessages") or []
            detail = "; ".join(str(m) for m in msgs[:2])
        except Exception:
            pass
        raise JiraError(
            f"Jira: HTTP {status} on {path}" + (f" — {detail}" if detail else ""),
            status)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise JiraError(
            f"Jira: non-JSON response from {site} (is this a Jira Cloud site?)",
            status) from None


# ————— ADF (Atlassian Document Format) → plain text —————


def adf_to_text(node) -> str:
    """Walk an ADF json tree; keep paragraphs, headings, bullets, code."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type", "")
    content = node.get("content") or []
    inner = "".join(adf_to_text(c) for c in content)
    if ntype == "text":
        return node.get("text", "")
    if ntype == "hardBreak":
        return "\n"
    if ntype == "mention":
        return (node.get("attrs") or {}).get("text", "@?")
    if ntype == "emoji":
        return (node.get("attrs") or {}).get("shortName", "")
    if ntype == "inlineCard":
        return (node.get("attrs") or {}).get("url", "")
    if ntype == "paragraph":
        return inner + "\n"
    if ntype == "heading":
        level = int((node.get("attrs") or {}).get("level") or 1)
        return "\n" + "#" * level + " " + inner + "\n"
    if ntype in ("bulletList", "orderedList"):
        return inner
    if ntype == "listItem":
        body = inner.strip("\n")
        return "- " + body.replace("\n", "\n  ") + "\n"
    if ntype == "codeBlock":
        return "\n```\n" + inner + "\n```\n"
    if ntype == "blockquote":
        return "\n" + "\n".join("> " + ln for ln in inner.strip("\n").split("\n")) + "\n"
    if ntype == "rule":
        return "\n---\n"
    if ntype in ("table", "tableRow"):
        return inner + ("\n" if ntype == "tableRow" else "")
    if ntype in ("tableCell", "tableHeader"):
        return inner.strip("\n") + " | "
    # doc, panel, expand, mediaGroup, unknown containers: recurse
    return inner


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ————— cursor / JQL merge —————


def _cursor_to_jql_ts(cursor: str) -> str:
    """ISO timestamp → Jira JQL datetime literal ('yyyy-MM-dd HH:mm', minutes)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", cursor)
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"


def _merge_jql(user_jql: str, cursor: str | None) -> str:
    jql = (user_jql or "").strip() or DEFAULT_JQL
    ts = _cursor_to_jql_ts(cursor or "")
    if not ts:
        return jql
    # strip any ORDER BY, AND the cursor in, re-order ascending so pagination
    # walks oldest→newest updated issues
    m = re.split(r"(?i)\bORDER\s+BY\b", jql, maxsplit=1)
    where = m[0].strip()
    clause = f'updated > "{ts}"'
    where = f"({where}) AND {clause}" if where else clause
    return where + " ORDER BY updated ASC"


# ————— contract surface —————


def validate(config: dict) -> str | None:
    """Cheap auth check: /rest/api/3/myself. None = ok, str = honest error."""
    try:
        data = _get(config, "/rest/api/3/myself")
    except JiraError as e:
        return str(e)
    if not isinstance(data, dict) or not data.get("accountId"):
        return "Jira: unexpected response from /myself (check the site URL)"
    return None


def _issue_to_item(issue: dict) -> dict:
    key = issue.get("key") or str(issue.get("id"))
    f = issue.get("fields") or {}
    summary = f.get("summary") or ""
    parts: list[str] = []
    desc = _clean(adf_to_text(f.get("description")))
    if desc:
        parts.append(desc)
    labels = f.get("labels") or []
    if labels:
        parts.append("Labels: " + ", ".join(labels))
    comments = ((f.get("comment") or {}).get("comments")) or []
    if comments:
        lines = ["", "## Comments"]
        for c in comments:
            author = ((c.get("author") or {}).get("displayName")) or "unknown"
            body = _clean(adf_to_text(c.get("body")))
            if body:
                lines.append(f"- {author}: {body}")
        parts.append("\n".join(lines))
    updated = f.get("updated") or ""
    return {
        "path": key,
        "title": f"{key}: {summary}" if summary else key,
        "body": "\n\n".join(parts).strip(),
        "updated_at": updated,
        "hash_hint": updated or None,
    }


def list_items(config: dict, cursor: str | None) -> tuple[list[dict], str | None]:
    """Search issues via JQL. Cursor = max `updated` ISO timestamp seen; on
    incremental runs `updated > cursor` is ANDed into the JQL server-side
    (minute precision — same-minute re-syncs may repeat an issue, which the
    hash pipeline dedupes)."""
    jql = _merge_jql(config.get("jql") or "", cursor)
    items: list[dict] = []
    max_seen = cursor or ""
    start = 0
    for _ in range(MAX_PAGES):
        data = _get(config, "/rest/api/3/search", {
            "jql": jql,
            "fields": "summary,description,comment,updated,labels",
            "startAt": start,
            "maxResults": PAGE_SIZE,
        })
        issues = data.get("issues") or []
        for issue in issues:
            item = _issue_to_item(issue)
            if item["updated_at"] > max_seen:
                max_seen = item["updated_at"]
            items.append(item)
        start += len(issues)
        total = data.get("total")
        if not issues or (isinstance(total, int) and start >= total):
            break
    return items, (max_seen or None)

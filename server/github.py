"""Mari Cloud — minimal GitHub REST v3 client (stdlib urllib, no deps).

Token comes from config (github.token / MARI_GITHUB_TOKEN). Every helper
raises GithubError with a safe message (never the token) on failure; callers
decide whether to degrade. Rate limits surface as GithubError too.
"""

from __future__ import annotations

import base64
import contextvars
import json
import typing as t
import urllib.error
import urllib.parse
import urllib.request

import config

API = "https://api.github.com"
_TOKEN_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mari_github_token_override", default=""
)


class GithubError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def token() -> str:
    return (_TOKEN_OVERRIDE.get() or config.get("github", "token") or "").strip()


def push_token(value: str):
    """Use one source's stored token in this worker/context only."""
    return _TOKEN_OVERRIDE.set((value or "").strip())


def pop_token(state) -> None:
    _TOKEN_OVERRIDE.reset(state)


def _request(path: str, params: dict | None = None) -> tuple[t.Any, dict]:
    """GET a GitHub API path; returns (parsed json, response headers)."""
    tok = token()
    if not tok:
        raise GithubError("no GitHub token configured", 401)
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "mari-cloud-sync",
    })
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            return json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            raise GithubError(f"GitHub rate limit exhausted (resets at {e.headers.get('X-RateLimit-Reset', '?')})", 403) from None
        raise GithubError(f"GitHub API {e.code} on {path}", e.code) from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise GithubError(f"GitHub unreachable: {getattr(e, 'reason', e).__class__.__name__}", 0) from None


def _paginate(path: str, params: dict, max_pages: int = 10) -> tuple[list[dict], bool]:
    """Fetch up to max_pages of 100. Returns (rows, truncated) — truncated is
    True when the safety cap was hit with more pages likely remaining, so
    callers can avoid advancing cursors past what was actually fetched."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        rows, _ = _request(path, {**params, "per_page": 100, "page": page})
        if not isinstance(rows, list):
            return out, False
        out.extend(rows)
        if len(rows) < 100:
            return out, False
    return out, True


# ————— repos / tree / blobs —————


def list_repos() -> list[dict]:
    """Repos visible to the token, most recently updated first."""
    rows, _ = _paginate("/user/repos", {"sort": "updated", "direction": "desc"}, max_pages=3)
    return rows


def head_sha(repo: str, branch: str) -> str:
    data, _ = _request(f"/repos/{repo}/commits/{urllib.parse.quote(branch)}")
    return data["sha"]


def default_branch(repo: str) -> str:
    data, _ = _request(f"/repos/{repo}")
    return data.get("default_branch") or "main"


def team(org: str, slug: str) -> dict:
    """An org team by slug. Raises GithubError(404) when it does not exist or
    the token cannot see it — used to validate a team before saving it."""
    data, _ = _request(f"/orgs/{urllib.parse.quote(org)}/teams/{urllib.parse.quote(slug)}")
    return data


def get_tree(repo: str, ref: str) -> list[dict]:
    """Recursive tree at ref: [{path, sha, type, size}, …] (blobs only)."""
    data, _ = _request(f"/repos/{repo}/git/trees/{urllib.parse.quote(ref)}", {"recursive": "1"})
    return [n for n in data.get("tree", []) if n.get("type") == "blob"]


def get_blob(repo: str, sha: str) -> str:
    """Blob content decoded to text ('' for binary)."""
    data, _ = _request(f"/repos/{repo}/git/blobs/{sha}")
    raw = base64.b64decode(data.get("content", "") or "")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


# ————— issues / PRs / comments / commits —————


def list_issues(repo: str, since: str = "") -> tuple[list[dict], bool]:
    """Issues AND pull requests (GitHub lists PRs as issues), updated since ts.
    Returns (rows, truncated) — truncated means the 50-page safety cap was hit
    and the caller must not advance its cursor past the newest row fetched."""
    params: dict = {"state": "all", "sort": "updated", "direction": "asc"}
    if since:
        params["since"] = since
    return _paginate(f"/repos/{repo}/issues", params, max_pages=50)


def list_issue_comments(repo: str, number: int, limit: int = 30) -> list[dict]:
    rows, _ = _paginate(f"/repos/{repo}/issues/{number}/comments", {}, max_pages=(limit + 99) // 100)
    return rows[-limit:]  # latest N


def list_commits(repo: str, branch: str, since: str = "") -> tuple[list[dict], bool]:
    """Commits on branch since ts (newest first). Returns (rows, truncated) —
    same cursor contract as list_issues."""
    params: dict = {"sha": branch}
    if since:
        params["since"] = since
    return _paginate(f"/repos/{repo}/commits", params, max_pages=50)

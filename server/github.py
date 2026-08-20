"""Mari — minimal GitHub REST v3 client (stdlib urllib, no deps).

Token comes from config (github.token / MARI_GITHUB_TOKEN). Every helper
raises GithubError with a safe message (never the token) on failure; callers
decide whether to degrade. Rate limits surface as GithubError too.
"""

from __future__ import annotations

import contextvars
import json
import time
import typing as t
import urllib.error
import urllib.parse
import urllib.request

import config
from connectors._protocol import call_with_retry
from mari_components.connectors import (
    GitHubConfig as ComponentGitHubConfig,
    github_blob as component_github_blob,
    github_commits as component_github_commits,
    github_head as component_github_head,
    github_issue_comments as component_github_issue_comments,
    github_issues as component_github_issues,
    github_repository as component_github_repository,
    github_tree as component_github_tree,
    list_github_repositories as component_list_github_repositories,
)
from mari_components.http import HttpRequest as ComponentHttpRequest, HttpResponse as ComponentHttpResponse

API = "https://api.github.com"
_TOKEN_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mari_github_token_override", default=""
)


class GithubError(Exception):
    def __init__(self, message: str, status: int = 0, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def token() -> str:
    return (_TOKEN_OVERRIDE.get() or config.get("github", "token") or "").strip()


def push_token(value: str):
    """Use one source's stored token in this worker/context only."""
    return _TOKEN_OVERRIDE.set((value or "").strip())


def pop_token(state) -> None:
    _TOKEN_OVERRIDE.reset(state)


def _request_once(path: str, params: dict | None = None) -> tuple[t.Any, dict]:
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
            reset = e.headers.get("X-RateLimit-Reset")
            retry_after = max(0.0, float(reset) - time.time()) if reset else None
            raise GithubError("GitHub rate limit exhausted", 403, retry_after) from None
        retry_after = e.headers.get("Retry-After")
        delay = float(retry_after) if retry_after and retry_after.isdigit() else None
        if e.code == 429:
            raise GithubError(f"GitHub API {e.code} on {path}: rate limit", e.code, delay) from None
        raise GithubError(f"GitHub API {e.code} on {path}", e.code) from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise GithubError(f"GitHub unreachable: {getattr(e, 'reason', e).__class__.__name__}", 0) from None


_RETRY_SLEEP = time.sleep


def _request(path: str, params: dict | None = None) -> tuple[t.Any, dict]:
    """GET with the connector-wide bounded retry policy."""
    return call_with_retry(lambda: _request_once(path, params), sleep=_RETRY_SLEEP)


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


def _component_http(request: ComponentHttpRequest) -> ComponentHttpResponse:
    parsed = urllib.parse.urlparse(request.url)
    params = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
    value, headers = _request(parsed.path, params or None)
    return ComponentHttpResponse(200, headers, json.dumps(value).encode())


def _component_config(repo: str, branch: str = "") -> ComponentGitHubConfig:
    return ComponentGitHubConfig(token(), repo, branch)


# ————— repos / tree / blobs —————


def list_repos() -> list[dict]:
    """Repos visible to the token, most recently updated first."""
    return list(component_list_github_repositories(token(), http=_component_http, page_limit=3))


def head_sha(repo: str, branch: str) -> str:
    return component_github_head(_component_config(repo, branch), branch, http=_component_http)


def default_branch(repo: str) -> str:
    data = component_github_repository(_component_config(repo), http=_component_http)
    return data.get("default_branch") or "main"


def team(org: str, slug: str) -> dict:
    """An org team by slug. Raises GithubError(404) when it does not exist or
    the token cannot see it — used to validate a team before saving it."""
    data, _ = _request(f"/orgs/{urllib.parse.quote(org)}/teams/{urllib.parse.quote(slug)}")
    return data


class TreeListing(list):
    """List-compatible tree result carrying whether enumeration was complete."""

    def __init__(self, values=(), *, complete: bool = True):
        super().__init__(values)
        self.complete = complete


def get_tree(repo: str, ref: str, request_cap: int = 2000,
             entry_cap: int = 250_000) -> TreeListing:
    """Enumerate a tree without trusting GitHub's truncated recursive result."""
    rows, complete = component_github_tree(
        _component_config(repo), ref, http=_component_http, request_limit=request_cap)
    if len(rows) >= entry_cap:
        rows, complete = rows[:entry_cap], False
    return TreeListing(rows, complete=complete)


def get_blob(repo: str, sha: str) -> str:
    """Blob content decoded to text ('' for binary)."""
    return component_github_blob(_component_config(repo), sha, http=_component_http)


# ————— issues / PRs / comments / commits —————


def list_issues(repo: str, since: str = "") -> tuple[list[dict], bool]:
    """Issues AND pull requests (GitHub lists PRs as issues), updated since ts.
    Returns (rows, truncated) — truncated means the 50-page safety cap was hit
    and the caller must not advance its cursor past the newest row fetched."""
    rows, complete = component_github_issues(
        _component_config(repo), since, http=_component_http, page_limit=50)
    return list(rows), not complete


def list_issue_comments(repo: str, number: int, limit: int = 30) -> list[dict]:
    return list(component_github_issue_comments(
        _component_config(repo), number, http=_component_http, limit=limit))


def list_commits(repo: str, branch: str, since: str = "") -> tuple[list[dict], bool]:
    """Commits on branch since ts (newest first). Returns (rows, truncated) —
    same cursor contract as list_issues."""
    rows, complete = component_github_commits(
        _component_config(repo, branch), branch, since, http=_component_http, page_limit=50)
    return list(rows), not complete

"""Small, stateless helpers for interactive GitHub knowledge destinations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import urllib.parse

from mari_components.errors import PermanentFailure
from mari_components.http import HttpRequest, HttpTransport


def mentions_bot(body: str, bot_login: str = "mari") -> bool:
    """A bare @login mention anywhere in the text, without requiring intent."""
    login = bot_login.strip().lstrip("@") or "mari"
    return bool(re.search(rf"(?<![\w-])@{re.escape(login)}(?![\w-])", body, re.I))


def requests_fact_validation(body: str, bot_login: str = "mari") -> bool:
    """Recognize a request for a fact check without product state.

    A bare @mention is enough on its own; explicit validate/verify/check/review
    "facts" phrasing is still recognized, it is just no longer required.
    """
    return mentions_bot(body, bot_login)


@dataclass(frozen=True, slots=True)
class GitHubCommentTarget:
    token: str
    repository: str
    number: int


def post_github_comment(target: GitHubCommentTarget, body: str, *, http: HttpTransport) -> dict:
    """Post one issue/PR comment through an injected HTTP transport."""
    request = HttpRequest(
        "POST",
        "https://api.github.com/repos/"
        f"{urllib.parse.quote(target.repository, safe='/')}/issues/{int(target.number)}/comments",
        {
            "Authorization": f"Bearer {target.token.strip()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mari-components",
        },
        json.dumps({"body": body}).encode(),
    )
    response = http(request)
    if response.status < 200 or response.status >= 300:
        raise PermanentFailure(f"GitHub comment failed with HTTP {response.status}")
    try:
        value = json.loads(response.body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PermanentFailure("GitHub comment response is invalid") from error
    if not isinstance(value, dict) or not value.get("id"):
        raise PermanentFailure("GitHub comment response is invalid")
    return value

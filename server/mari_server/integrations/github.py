"""Mari-owned configuration and transport for reusable GitHub operations."""

from __future__ import annotations

from mari_server import config
from mari_components.connectors import (
    GitHubConfig, github_repository, list_github_repositories,
    validate_github_team,
)
from mari_server.integrations.connector_provider import http_transport


def configured_token() -> str:
    return str(config.get("github", "token") or "").strip()


def repositories(token: str) -> tuple[dict, ...]:
    return list_github_repositories(token.strip(), http=http_transport)


def repository(token: str, slug: str) -> dict:
    return github_repository(GitHubConfig(token.strip(), slug.strip()), http=http_transport)


def default_branch(token: str, slug: str) -> str:
    return str(repository(token, slug).get("default_branch") or "main")


def team_is_valid(token: str, organization: str, team: str) -> bool:
    return validate_github_team(token.strip(), organization, team, http=http_transport).ok

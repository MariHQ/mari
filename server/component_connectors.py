"""Compatibility facade for the component connector provider adapter."""

from mari_server.infrastructure.connector_provider import (
    ComponentPollPage,
    _collect,
    _cursor,
    _http,
    functions,
    poll_pages,
    validate_config,
)

__all__ = [
    "ComponentPollPage", "_collect", "_cursor", "_http", "functions",
    "poll_pages", "validate_config",
]

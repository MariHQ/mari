"""Navigable product surfaces and validation, independent of HTTP transport."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductSurface:
    path: str
    label: str


#: Every surface the agent may navigate to, and the words it may use for them.
#:
#: `/workflows` is one page with two tabs: the workflows Mari observed itself
#: running, and the answers approved out of them. `/trajectories` and
#: `/answers` are the routes that page used to have; they still resolve (the
#: console redirects them, carrying their query through), so an agent working
#: from an older transcript, a bookmark, or a stored href still lands.
PRODUCT_SURFACES = (
    ProductSurface("/", "Home"), ProductSurface("/knowledge", "Knowledge"),
    ProductSurface("/facts", "Facts"), ProductSurface("/decisions", "Decisions"),
    ProductSurface("/lineage", "Lineage"),
    ProductSurface("/workflows", "Workflows"),
    ProductSurface("/workflows?tab=answers", "Approved answers"),
    ProductSurface("/publish", "Documentation destinations"),
    ProductSurface("/publish?tab=mcp", "MCP servers"),
    ProductSurface("/publish?tab=bots", "Bots"), ProductSurface("/insights", "Analytics"),
    ProductSurface("/trajectories", "Observed workflows (moved to Workflows)"),
    ProductSurface("/answers", "Approved answers (moved to Workflows)"),
    ProductSurface("/library", "Library"), ProductSurface("/sources", "Sources"),
    ProductSurface("/audit", "Repository audit"),
    ProductSurface("/preferences", "Preferences"), ProductSurface("/welcome", "Onboarding"),
    ProductSurface("/settings/general", "General settings"),
    ProductSurface("/settings/models", "Model settings"),
    ProductSurface("/settings/design", "Design settings"),
    ProductSurface("/settings/members", "Members"),
    ProductSurface("/settings/api-keys", "API keys"),
    ProductSurface("/settings/audit", "Audit log"),
)

_BASES = {surface.path.partition("?")[0] for surface in PRODUCT_SURFACES}
_QUERY = re.compile(r"^[A-Za-z0-9_.\-]+=[A-Za-z0-9_.%\- ]*$")


def valid_navigation(path: str) -> bool:
    """Validate a client-side route without importing the web framework."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return False
    if any(character in path for character in ("\\", "..", "\n", "#")):
        return False
    base, _, query = path.partition("?")
    base = base.rstrip("/") or "/"
    if query and not all(_QUERY.match(part) for part in query.split("&") if part):
        return False
    return base in _BASES or base == "/knowledge/doc"

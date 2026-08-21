"""Project identity and authorization shared by HTTP, GraphQL and workers.

Authentication answers *who* a caller is.  This module separately resolves
which project that identity may act in and what its membership permits.  Keep
that distinction explicit: a valid Mari session is not, by itself, access to
any project's data.
"""

from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request
from mari_server.identity.context import (
    AccessContext, CAPABILITIES, ProjectMembership, capabilities_for_role,
    current_access, external_access, require_current_access, set_access, use_access,
)
from mari_server.persistence.postgres import identity


def memberships_for_user(user_id: int, conn_factory: Callable) -> list[ProjectMembership]:
    return [ProjectMembership(**dict(row)) for row in identity.memberships(user_id)]


def select_membership(memberships: list[ProjectMembership], requested: str | None,
                      *, required: bool = True) -> ProjectMembership | None:
    """Select an active membership by ID or slug.

    With no header, inference is intentionally limited to exactly one active
    membership.  This avoids a mutable session-level project and cross-tab
    races.  `required=False` is used by /auth/me so a multi-project user can
    discover their choices before selecting one.
    """
    requested = (requested or "").strip()
    if requested:
        match = next((m for m in memberships
                      if requested == str(m.project_id) or requested == m.project_slug), None)
        if match is None:
            raise HTTPException(403, "You do not have access to that project.")
        return match
    if len(memberships) == 1:
        return memberships[0]
    if not required:
        return None
    if not memberships:
        raise HTTPException(403, "You do not have access to an active project.")
    raise HTTPException(400, "Choose a project with the X-Mari-Project header.")


def resolve_access(user: dict, requested: str | None, conn_factory: Callable,
                   *, required: bool = True) -> tuple[AccessContext | None, list[ProjectMembership]]:
    # Memberships are read on every request, rather than copied into the
    # session, so disabling one revokes access immediately.
    memberships = memberships_for_user(int(user["id"]), conn_factory)
    membership = select_membership(memberships, requested, required=required)
    if membership is None:
        set_access(None)
        return None, memberships
    context = AccessContext(
        user_id=int(user["id"]), project_id=membership.project_id,
        project_slug=membership.project_slug, project_name=membership.project_name,
        role=membership.role, capabilities=capabilities_for_role(membership.role),
        principal_id=str(user["id"]),
    )
    set_access(context)
    return context, memberships


def require_project(request: Request) -> AccessContext:
    # Local import prevents the auth -> access -> auth cycle.
    from mari_server.identity import routes as auth
    user = auth.require_user(request)
    scope = getattr(request, "scope", None)
    # Memoization is request-local only. A subsequent request re-reads the live
    # membership, which is the revocation boundary.
    if isinstance(scope, dict) and "mari_access" in scope:
        context = scope["mari_access"]
        if context is None:
            raise HTTPException(403, "You do not have access to an active project.")
        set_access(context)
        return context
    context, _ = resolve_access(user, request.headers.get("X-Mari-Project"), auth._conn)
    if isinstance(scope, dict):
        scope["mari_access"] = context
    assert context is not None
    # REST dependencies are resolved inside the endpoint execution context.
    # Publish the resolved membership there as well as on the request scope so
    # project-scoped helpers (search, retrieval, audit) cannot observe an empty
    # ContextVar on the first non-GraphQL request for a project.
    set_access(context)
    return context


def require_capability(capability: str):
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {capability}")

    def dependency(request: Request) -> AccessContext:
        context = require_project(request)
        if not context.allows(capability):
            raise HTTPException(403, f"This action requires {capability}.")
        return context

    return dependency

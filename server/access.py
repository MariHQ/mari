"""Project identity and authorization shared by HTTP, GraphQL and workers.

Authentication answers *who* a caller is.  This module separately resolves
which project that identity may act in and what its membership permits.  Keep
that distinction explicit: a valid Mari session is not, by itself, access to
any project's data.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, FrozenSet, Mapping

from fastapi import HTTPException, Request


CAPABILITIES: FrozenSet[str] = frozenset({
    "knowledge.read", "knowledge.write", "review.approve",
    "automation.run", "automation.manage", "source.sync", "source.manage",
    "destination.manage", "member.manage", "settings.manage",
})

# Membership roles are deliberately translated in one place.  The legacy
# names remain accepted while existing callers and rows migrate.
ROLE_CAPABILITIES: Mapping[str, FrozenSet[str]] = {
    "owner": CAPABILITIES,
    "admin": CAPABILITIES,
    "manager": frozenset({
        "knowledge.read", "knowledge.write", "review.approve",
        "automation.run", "automation.manage", "source.sync",
    }),
    "member": frozenset({"knowledge.read", "knowledge.write", "automation.run"}),
    "user": frozenset({"knowledge.read", "knowledge.write", "automation.run"}),
    "viewer": frozenset({"knowledge.read"}),
}


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    project_id: int
    project_slug: str
    project_name: str
    role: str
    status: str = "active"

    def as_dict(self) -> dict:
        return {
            "id": self.project_id,
            "slug": self.project_slug,
            "name": self.project_name,
            "role": self.role,
            "status": self.status,
            "capabilities": sorted(capabilities_for_role(self.role)),
        }


@dataclass(frozen=True, slots=True)
class AccessContext:
    user_id: int
    project_id: int
    project_slug: str
    project_name: str
    role: str
    capabilities: FrozenSet[str]
    principal_type: str = "user"
    principal_id: str = ""
    principals: FrozenSet[str] = frozenset()

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    def project_dict(self) -> dict:
        return {"id": self.project_id, "slug": self.project_slug,
                "name": self.project_name, "role": self.role}


CURRENT_ACCESS: contextvars.ContextVar[AccessContext | None] = contextvars.ContextVar(
    "mari_access", default=None)


def set_access(value: AccessContext | None) -> None:
    CURRENT_ACCESS.set(value)


def current_access() -> AccessContext | None:
    return CURRENT_ACCESS.get()


def require_current_access() -> AccessContext:
    context = current_access()
    if context is None:
        raise RuntimeError("Project access context is required for this operation")
    return context


def external_access(project_id: int, project_slug: str, project_name: str,
                    principal_type: str, principal_id: str,
                    capabilities: FrozenSet[str] | None = None,
                    principals: FrozenSet[str] | None = None) -> AccessContext:
    """Build a narrowly scoped context for a verified external principal."""
    return AccessContext(
        user_id=0, project_id=int(project_id), project_slug=project_slug,
        project_name=project_name, role="external",
        capabilities=capabilities or frozenset({"knowledge.read"}),
        principal_type=principal_type, principal_id=principal_id,
        principals=principals or frozenset(),
    )


@contextmanager
def use_access(context: AccessContext):
    token = CURRENT_ACCESS.set(context)
    try:
        yield context
    finally:
        CURRENT_ACCESS.reset(token)


def capabilities_for_role(role: str) -> FrozenSet[str]:
    return ROLE_CAPABILITIES.get((role or "").lower(), frozenset())


def _memberships(conn, user_id: int) -> list[ProjectMembership]:
    rows = conn.execute(
        """SELECT p.id AS project_id, p.slug AS project_slug,
                  p.name AS project_name, pm.role, pm.status
             FROM project_members pm
             JOIN projects p ON p.id = pm.project_id
            WHERE pm.user_id = %s AND pm.status = 'active'
              AND p.status = 'active'
            ORDER BY p.id""", (user_id,)).fetchall()
    return [ProjectMembership(**dict(row)) for row in rows]


def memberships_for_user(user_id: int, conn_factory: Callable) -> list[ProjectMembership]:
    with conn_factory() as conn:
        return _memberships(conn, user_id)


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
    import auth
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

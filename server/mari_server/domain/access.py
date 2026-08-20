"""Project-scoped identity shared by application and infrastructure code."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import FrozenSet, Mapping


CAPABILITIES: FrozenSet[str] = frozenset({
    "knowledge.read", "knowledge.write", "review.approve",
    "automation.run", "automation.manage", "source.sync", "source.manage",
    "destination.manage", "member.manage", "settings.manage",
})

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


def capabilities_for_role(role: str) -> FrozenSet[str]:
    return ROLE_CAPABILITIES.get((role or "").lower(), frozenset())


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

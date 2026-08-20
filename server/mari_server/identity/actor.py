"""Request-independent caller identity context."""

from __future__ import annotations

import contextvars


SERVICE_ACTOR = "Mari"
CALLER: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "mari_caller", default=None)


def set_caller(user: dict | None) -> None:
    CALLER.set(user)


def caller() -> dict | None:
    return CALLER.get()


def actor_name() -> str:
    user = CALLER.get()
    name = (user or {}).get("name") if isinstance(user, dict) else None
    return str(name) if name else SERVICE_ACTOR

"""Typed connector polling contract with a legacy tuple adapter.

Provider modules may return :class:`PollResult` today without breaking callers
that still unpack ``items, cursor``.  The generic worker also accepts the old
two-tuple while connectors migrate one at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import time
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable


class ErrorKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class ACLMetadata:
    """Visibility reported by a provider.

    Persistence/enforcement is intentionally outside this module: callers must
    not interpret absent ACL metadata as public content.
    """

    visibility: str = "connector_scope"
    principals: tuple[str, ...] = ()


@dataclass(frozen=True)
class PollItem:
    path: str
    title: str
    body: str
    updated_at: str = ""
    hash_hint: str | None = None
    source_url: str | None = None
    acl: ACLMetadata | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "body": self.body,
            "updated_at": self.updated_at,
            "hash_hint": self.hash_hint,
            "source_url": self.source_url,
            "acl": self.acl,
        }


@dataclass
class PollResult:
    items: list[dict[str, Any] | PollItem]
    cursor: str | None
    snapshot_complete: bool = True
    tombstones: list[str] = field(default_factory=list)
    checkpoint: str | None = None

    def __iter__(self):
        """Keep ``items, cursor = list_items(...)`` callers working."""
        yield self.items
        yield self.cursor


@runtime_checkable
class Connector(Protocol):
    PROVIDER: dict[str, Any]

    def validate(self, config: dict[str, Any]) -> str | None: ...

    def list_items(self, config: dict[str, Any], cursor: str | None) -> PollResult: ...


def adapt_poll_result(value: Any) -> PollResult:
    if isinstance(value, PollResult):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        items, cursor = value
        return PollResult(items=list(items or []), cursor=cursor)
    raise TypeError("connector list_items must return PollResult or (items, cursor)")


class ConnectorCallError(RuntimeError):
    def __init__(self, message: str, kind: ErrorKind, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def classify_error(error: BaseException) -> ErrorKind:
    if isinstance(error, ConnectorCallError):
        return error.kind
    status = int(getattr(error, "status", 0) or 0)
    text = str(error).lower()
    textual_status = re.search(r"(?:http|status)[^0-9]{0,8}([45][0-9]{2})", text)
    if not status and textual_status:
        status = int(textual_status.group(1))
    if status == 429 or "rate limit" in text or "ratelimited" in text:
        return ErrorKind.RATE_LIMIT
    if status in (401, 403) or any(x in text for x in ("unauthorized", "forbidden", "invalid token")):
        return ErrorKind.AUTH
    if status in (408, 425) or status >= 500 or isinstance(error, (ConnectionError, TimeoutError)):
        return ErrorKind.TRANSIENT
    if any(x in text for x in ("timeout", "timed out", "unreachable", "network error", "temporarily unavailable")):
        return ErrorKind.TRANSIENT
    return ErrorKind.PERMANENT


T = TypeVar("T")


def call_with_retry(fn: Callable[[], T], *, attempts: int = 3,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """Retry only rate-limit/transient failures with bounded exponential delay."""
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception as error:  # classification decides whether it is safe
            last = error
            kind = classify_error(error)
            if kind not in (ErrorKind.RATE_LIMIT, ErrorKind.TRANSIENT) or attempt + 1 >= attempts:
                raise
            requested = getattr(error, "retry_after", None)
            delay = float(requested) if requested is not None else min(2 ** attempt, 8)
            sleep(max(0.0, min(delay, 30.0)))
    assert last is not None
    raise last

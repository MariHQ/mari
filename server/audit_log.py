"""Tamper-evident, append-only enterprise audit records in Apache Iceberg."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
import typing as t
import uuid
from dataclasses import asdict, dataclass, field

import pyarrow as pa

from iceberg import IcebergWarehouse, warehouse


AUDIT_TABLE = "mari.audit_events"
AUDIT_SCHEMA = pa.schema([
    pa.field("event_id", pa.string(), nullable=False),
    pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("project_id", pa.int64(), nullable=False),
    pa.field("actor_type", pa.string(), nullable=False),
    pa.field("actor_id", pa.string(), nullable=False),
    pa.field("actor_name", pa.string(), nullable=False),
    pa.field("action", pa.string(), nullable=False),
    pa.field("resource_type", pa.string(), nullable=False),
    pa.field("resource_id", pa.string(), nullable=False),
    pa.field("outcome", pa.string(), nullable=False),
    pa.field("reason", pa.string(), nullable=False),
    pa.field("request_id", pa.string(), nullable=False),
    pa.field("correlation_id", pa.string(), nullable=False),
    pa.field("detail_json", pa.string(), nullable=False),
    pa.field("previous_hash", pa.string(), nullable=False),
    pa.field("event_hash", pa.string(), nullable=False),
])

_SENSITIVE = re.compile(r"token|secret|password|authorization|cookie|api[_-]?key", re.I)


def redact(value: t.Any) -> t.Any:
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if _SENSITIVE.search(str(key)) else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class AuditEvent:
    project_id: int
    actor_type: str
    actor_id: str
    actor_name: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str = "success"
    reason: str = ""
    request_id: str = ""
    correlation_id: str = ""
    detail: dict[str, t.Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def __post_init__(self) -> None:
        if self.project_id < 0:
            raise ValueError("project_id cannot be negative")
        if self.outcome not in {"success", "failure", "denied", "manual"}:
            raise ValueError("invalid audit outcome")
        if not self.action or not self.resource_type:
            raise ValueError("audit action and resource type are required")


def _canonical(row: dict[str, t.Any]) -> bytes:
    serializable = dict(row)
    when = serializable.get("occurred_at")
    if isinstance(when, dt.datetime):
        serializable["occurred_at"] = when.astimezone(dt.timezone.utc).isoformat()
    return json.dumps(serializable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class IcebergAuditTrail:
    """Serializes local writers and relies on Iceberg optimistic commits across writers."""

    def __init__(self, store: IcebergWarehouse | None = None):
        self.store = store or warehouse()
        self._lock = threading.RLock()
        try:
            self.store.catalog.load_table(AUDIT_TABLE)
        except Exception:
            self.store.catalog.create_table(AUDIT_TABLE, schema=AUDIT_SCHEMA)

    def _last_hash(self) -> str:
        rows = self.store.catalog.load_table(AUDIT_TABLE).scan(
            selected_fields=("occurred_at", "event_id", "event_hash")).to_arrow().to_pylist()
        if not rows:
            return ""
        rows.sort(key=lambda row: (row["occurred_at"], row["event_id"]))
        return str(rows[-1]["event_hash"])

    def append(self, event: AuditEvent) -> dict[str, t.Any]:
        with self._lock:
            previous = self._last_hash()
            row = asdict(event)
            row["detail_json"] = json.dumps(redact(row.pop("detail")), sort_keys=True,
                                             separators=(",", ":"), default=str)
            row["previous_hash"] = previous
            row["event_hash"] = hashlib.sha256(previous.encode() + _canonical(row)).hexdigest()
            self.store.catalog.load_table(AUDIT_TABLE).append(
                pa.Table.from_pylist([row], schema=AUDIT_SCHEMA),
                snapshot_properties={"mari.audit-event-id": event.event_id},
            )
            return row

    def rows(self, project_id: int | None = None) -> list[dict[str, t.Any]]:
        rows = self.store.catalog.load_table(AUDIT_TABLE).scan().to_arrow().to_pylist()
        if project_id is not None:
            rows = [row for row in rows if row["project_id"] == project_id]
        rows.sort(key=lambda row: (row["occurred_at"], row["event_id"]))
        return rows

    @staticmethod
    def verify(rows: list[dict[str, t.Any]]) -> bool:
        previous = ""
        for original in sorted(rows, key=lambda row: (row["occurred_at"], row["event_id"])):
            row = dict(original)
            actual = row.pop("event_hash")
            if row.get("previous_hash") != previous:
                return False
            if hashlib.sha256(previous.encode() + _canonical(row)).hexdigest() != actual:
                return False
            previous = actual
        return True

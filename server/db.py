"""Mari — database helpers (shared by all API modules).

Connection ownership lives in ``mari_server.infrastructure.postgres``.  This
module remains the small query/audit facade while legacy callers are migrated.
"""

from __future__ import annotations

import json
import typing as t

import auth as auth_module
import access as access_module
import observability
from mari_server.domain.audit import AuditEvent, chained_row
from mari_server.infrastructure import postgres

# ————— who did this —————
#
# AUTH-5: the audit log used to attribute every write to one hardcoded person
# ("Daniel Henneberger"), including approvals nobody by that name granted.
# `auth.current_user` publishes the caller for the duration of the request, so
# `actor_name()` returns the person actually signed in. Work with no human
# behind it — the ingest poller, a scheduled flow, a webhook — records
# SERVICE_ACTOR ("Mari"), which is a true statement about who did it.
SERVICE_ACTOR = auth_module.SERVICE_ACTOR
actor_name = auth_module.actor_name
caller = auth_module.caller

# Shared pool for the request path (q/q1/exec_). Long-lived background workers
# (ingest/connect_sync/flowengine) keep their own dedicated connections — they
# hold transactions open for minutes and must not starve the pool.
def open_pool() -> None:
    postgres.pool()


def close_pool() -> None:
    postgres.close_pool()


def connect():
    return postgres.connect()


def q(sql: str, args: tuple = ()) -> list[dict]:
    with postgres.pool().connection() as conn:
        return conn.execute(sql, args).fetchall()


def q1(sql: str, args: tuple = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def exec_(sql: str, args: tuple = ()) -> None:
    with postgres.pool().connection() as conn:
        conn.execute(sql, args)


def transaction(fn: t.Callable[[t.Any], t.Any]) -> t.Any:
    """Run a small unit of work atomically on one pooled connection."""
    return postgres.transaction(fn)


def project_id() -> int:
    """Current tenant key. Never infer a default project on a data path."""
    return access_module.require_current_access().project_id


def pq(sql: str, args: tuple = ()) -> list[dict]:
    """Execute project SQL whose first placeholder is the project_id."""
    return q(sql, (project_id(), *args))


def pq1(sql: str, args: tuple = ()) -> dict | None:
    rows = pq(sql, args)
    return rows[0] if rows else None


def pexec(sql: str, args: tuple = ()) -> None:
    """Execute a project write whose first placeholder is project_id."""
    exec_(sql, (project_id(), *args))


def audit(verb: str, target: str, actor: str | None = None,
          detail: t.Sequence[tuple[str, t.Any]] | None = None, *,
          resource_type: str = "record", resource_id: str = "",
          outcome: str = "success", reason: str = "") -> None:
    """Record an access-log event. `detail` is the ordered label/value rows the
    console shows when the row is expanded — pass only facts the caller
    actually knows (the previous value of a field, the scope of a key). Order
    is preserved because it is stored as a jsonb array, not an object.

    `actor` defaults to whoever made the request; pass one only to override."""
    actor = actor or actor_name()
    rows = [{"label": str(lbl), "value": "" if val is None else str(val)}
            for lbl, val in (detail or []) if str(lbl)]
    scope = access_module.current_access()
    request_id, correlation_id = observability.request_context()
    current_user = auth_module.caller() or {}
    project = scope.project_id if scope else 0
    event = AuditEvent(
        project_id=project,
        actor_type=scope.principal_type if scope else "service",
        actor_id=scope.principal_id if scope else str(current_user.get("id") or "mari"),
        actor_name=actor, action=verb, resource_type=resource_type,
        resource_id=resource_id or target, outcome=outcome, reason=reason,
        request_id=request_id, correlation_id=correlation_id,
        detail={row["label"]: row["value"] for row in rows},
    )

    def append(conn):
        # Serialize only this project's chain. The canonical row and the UI
        # projection commit together in Postgres.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (8_041_700_000 + project,))
        prior = conn.execute(
            """SELECT event_hash FROM audit_events WHERE project_id = %s
                 ORDER BY occurred_at DESC, event_id DESC LIMIT 1""", (project,),
        ).fetchone()
        chained = chained_row(event, str(prior["event_hash"]) if prior else "")
        conn.execute(
            """INSERT INTO audit_events
               (event_id, occurred_at, project_id, actor_type, actor_id, actor_name,
                action, resource_type, resource_id, outcome, reason, request_id,
                correlation_id, detail_json, previous_hash, event_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s::jsonb, %s, %s)""",
            tuple(chained[key] for key in (
                "event_id", "occurred_at", "project_id", "actor_type", "actor_id",
                "actor_name", "action", "resource_type", "resource_id", "outcome",
                "reason", "request_id", "correlation_id", "detail_json",
                "previous_hash", "event_hash",
            )),
        )
        conn.execute(
            "INSERT INTO events (project_id, actor, verb, target, detail) VALUES (%s, %s, %s, %s, %s)",
            (project or None, actor, verb, target, json.dumps(rows)),
        )

    transaction(append)


def log_usage(kind: str, detail: str = "") -> None:
    """Honest usage counter (BOTS-CONTRACT.md §A): kinds are 'search',
    'chat_answer', 'site_view'. Shared by the search resolver and the bot/chat
    paths. Telemetry must never break the request it rides on."""
    try:
        exec_("INSERT INTO usage_log (kind, detail) VALUES (%s, %s)", (kind, (detail or "")[:120]))
    except Exception:  # noqa: BLE001
        pass


def jload(v: t.Any) -> t.Any:
    # psycopg may hand back jsonb already decoded (dict/list/bool/int/float).
    if isinstance(v, (list, dict, bool, int, float)):
        return v
    return json.loads(v or "null")


def ensure_schema() -> None:
    """Apply serialized, checksum-verified migrations before serving traffic."""
    from schema_migrations import migrate
    migrate(postgres.database_url())

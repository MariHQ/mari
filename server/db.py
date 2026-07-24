"""Mari Cloud — database helpers (shared by all API modules).

DB_URL comes from MARI_DB (default local Postgres); the DB_URL_REF dicts in
flowengine/auth/repoaudit are injected here so every module talks to the same
database. db.py must stay import-cycle-free: it never imports app/queries/mutations.
"""

from __future__ import annotations

import json
import os
import pathlib
import typing as t

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import auth as auth_module
import flowengine
import ingest
import repoaudit

DB_URL = os.environ.get("MARI_DB", "postgresql://localhost/mari_cloud")
flowengine.DB_URL_REF["url"] = DB_URL
ingest.DB_URL_REF["url"] = DB_URL
auth_module.DB_URL_REF["url"] = DB_URL
repoaudit.DB_URL_REF["url"] = DB_URL
ME = "Daniel Henneberger"

# Shared pool for the request path (q/q1/exec_). Long-lived background workers
# (ingest/connect_sync/flowengine) keep their own dedicated connections — they
# hold transactions open for minutes and must not starve the pool.
POOL = ConnectionPool(
    DB_URL,
    min_size=1,
    max_size=int(os.environ.get("MARI_DB_POOL_MAX", "10")),
    kwargs={"row_factory": dict_row},
    open=True,
    name="mari-api",
)


def q(sql: str, args: tuple = ()) -> list[dict]:
    with POOL.connection() as conn:
        return conn.execute(sql, args).fetchall()


def q1(sql: str, args: tuple = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def exec_(sql: str, args: tuple = ()) -> None:
    with POOL.connection() as conn:
        conn.execute(sql, args)


def audit(verb: str, target: str, actor: str = ME,
          detail: t.Sequence[tuple[str, t.Any]] | None = None) -> None:
    """Record an access-log event. `detail` is the ordered label/value rows the
    console shows when the row is expanded — pass only facts the caller
    actually knows (the previous value of a field, the scope of a key). Order
    is preserved because it is stored as a jsonb array, not an object."""
    rows = [{"label": str(lbl), "value": "" if val is None else str(val)}
            for lbl, val in (detail or []) if str(lbl)]
    exec_("INSERT INTO events (actor, verb, target, detail) VALUES (%s, %s, %s, %s)",
          (actor, verb, target, json.dumps(rows)))


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
    """Apply the complete schema (init.sql, idempotent) so startup, local
    `psql -f init.sql`, and the docker-compose db-init service all agree."""
    sql = (pathlib.Path(__file__).parent / "init.sql").read_text()
    with psycopg.connect(DB_URL) as conn:
        conn.execute(sql)

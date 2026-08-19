"""Small durable control-plane state that does not belong in Iceberg.

Knowledge, lineage, and audit history are durable analytical records. Login
sessions are short-lived coordination state: they need atomic lookup and
revocation, but they should not force the knowledge store to behave like an
OLTP database. This module keeps that state in a local SQLite database.

One connection is opened per operation. SQLite WAL mode permits concurrent
readers and serializes the uncommon login/logout writes without sharing a
connection across FastAPI worker threads.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sqlite3
import time
import typing as t


def path() -> pathlib.Path:
    configured = os.environ.get("MARI_CONTROL_DB", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser()
    data_dir = pathlib.Path(os.environ.get("MARI_DATA_DIR", ".mari")).expanduser()
    return data_dir / "control.sqlite3"


def _connect() -> sqlite3.Connection:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(target, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema() -> None:
    with contextlib.closing(_connect()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                expires_at  INTEGER NOT NULL,
                client_ip   TEXT NOT NULL DEFAULT '',
                user_agent  TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS sessions_user_idx
                ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS sessions_expiry_idx
                ON sessions(expires_at);
        """)
    try:
        os.chmod(path(), 0o600)
    except OSError:
        # Some mounted/container filesystems do not support chmod. SQLite still
        # remains usable; deployment manifests restrict the mounted volume.
        pass


def put_session(token: str, user_id: int, ttl_seconds: int, *,
                client_ip: str = "", user_agent: str = "",
                now: int | None = None) -> None:
    if not token or user_id <= 0 or ttl_seconds <= 0:
        raise ValueError("a session needs a token, positive user id, and positive TTL")
    created = int(time.time() if now is None else now)
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        conn.execute(
            """INSERT INTO sessions
               (token, user_id, created_at, expires_at, client_ip, user_agent)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(token) DO UPDATE SET
                 user_id=excluded.user_id, created_at=excluded.created_at,
                 expires_at=excluded.expires_at, client_ip=excluded.client_ip,
                 user_agent=excluded.user_agent""",
            (token, user_id, created, created + ttl_seconds,
             client_ip[:200], user_agent[:500]),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (created,))


def session(token: str, *, now: int | None = None) -> dict[str, t.Any] | None:
    if not token:
        return None
    current = int(time.time() if now is None else now)
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        row = conn.execute(
            """SELECT token, user_id, created_at, expires_at, client_ip, user_agent
               FROM sessions WHERE token = ? AND expires_at > ?""",
            (token, current),
        ).fetchone()
        if row is None:
            # Opportunistically remove the one expired token the caller tried.
            conn.execute("DELETE FROM sessions WHERE token = ? AND expires_at <= ?",
                         (token, current))
            return None
        return dict(row)


def revoke_session(token: str) -> bool:
    if not token:
        return False
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return cur.rowcount > 0


def revoke_user_sessions(user_id: int) -> int:
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cur.rowcount


def revoke_other_user_sessions(user_id: int, keep_token: str) -> int:
    """Revoke a user's other sessions after a credential change."""
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token <> ?",
            (user_id, keep_token),
        )
        return cur.rowcount


def cleanup(*, now: int | None = None) -> int:
    current = int(time.time() if now is None else now)
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (current,))
        return cur.rowcount


def health() -> dict[str, t.Any]:
    ensure_schema()
    with contextlib.closing(_connect()) as conn, conn:
        conn.execute("SELECT 1").fetchone()
        rows = conn.execute("SELECT count(*) AS n FROM sessions WHERE expires_at > ?",
                            (int(time.time()),)).fetchone()
    return {"ok": True, "path": str(path()), "active_sessions": int(rows["n"])}

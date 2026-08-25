"""PostgreSQL-backed transactional control state.

Sessions are operational records rather than enterprise knowledge, but using
the same managed PostgreSQL service gives them the same HA, backup, and restore
path as the rest of the product. These operations are short request/control-
plane queries, so they borrow from the pool instead of opening a new socket.
"""

from __future__ import annotations

import datetime as dt
import typing as t
from mari_server.persistence.postgres import connection as postgres


def _connect():
    return postgres.request_connection()


def _instant(value: int | float | None = None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)


def ensure_schema() -> None:
    """Verify migrations installed the control tables; never mutate at runtime."""
    with _connect() as conn:
        conn.execute("SELECT 1 FROM sessions LIMIT 0")


def put_session(token: str, user_id: int, ttl_seconds: int, *,
                client_ip: str = "", user_agent: str = "",
                now: int | float | None = None) -> None:
    if not token or user_id <= 0 or ttl_seconds <= 0:
        raise ValueError("a session needs a token, positive user id, and positive TTL")
    created = _instant(now)
    expires = created + dt.timedelta(seconds=ttl_seconds)
    with _connect() as conn:
        conn.execute(
            """INSERT INTO sessions
               (token, user_id, created_at, expires_at, client_ip, user_agent)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT(token) DO UPDATE SET
                 user_id=excluded.user_id, created_at=excluded.created_at,
                 expires_at=excluded.expires_at, client_ip=excluded.client_ip,
                 user_agent=excluded.user_agent""",
            (token, user_id, created, expires, client_ip[:200], user_agent[:500]),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at <= %s", (created,))


def session(token: str, *, now: int | float | None = None) -> dict[str, t.Any] | None:
    if not token:
        return None
    current = _instant(now)
    with _connect() as conn:
        row = conn.execute(
            """SELECT token, user_id, created_at, expires_at, client_ip, user_agent
               FROM sessions WHERE token = %s AND expires_at > %s""",
            (token, current),
        ).fetchone()
        if row is None:
            conn.execute(
                "DELETE FROM sessions WHERE token = %s AND expires_at <= %s",
                (token, current),
            )
            return None
        return dict(row)


def revoke_session(token: str) -> bool:
    if not token:
        return False
    with _connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE token = %s", (token,)).rowcount > 0


def revoke_user_sessions(user_id: int) -> int:
    with _connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,)).rowcount


def revoke_other_user_sessions(user_id: int, keep_token: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM sessions WHERE user_id = %s AND token <> %s",
            (user_id, keep_token),
        ).rowcount


def cleanup(*, now: int | float | None = None) -> int:
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM sessions WHERE expires_at <= %s", (_instant(now),),
        ).rowcount


def health() -> dict[str, t.Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM sessions WHERE expires_at > now()",
        ).fetchone()
    return {"ok": True, "backend": "postgresql", "active_sessions": int(row["n"])}

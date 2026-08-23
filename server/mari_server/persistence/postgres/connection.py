"""Process-owned PostgreSQL connections.

Configuration is resolved once by :mod:`config`.  Request work uses a lazy
pool; background jobs that may hold transactions use ``connect()``.  Merely
importing this module never opens a socket.
"""

from __future__ import annotations

import atexit
import threading
import typing as t

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from mari_server import settings as config


_LOCK = threading.Lock()
_POOL: ConnectionPool | None = None


def database_url() -> str:
    return str(config.get("database", "url"))


def connect() -> psycopg.Connection:
    """Open a dedicated connection for a long transaction/background job."""
    return psycopg.connect(database_url(), row_factory=dict_row)


def pool() -> ConnectionPool:
    """Return the request pool, creating it without opening it at import time."""
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ConnectionPool(
                database_url(), min_size=1,
                max_size=int(config.get("database", "pool_max", 10)),
                kwargs={"row_factory": dict_row}, open=False, name="mari-api",
                # Validate at checkout: after a database restart the pool's
                # idle connections are dead, and handing one to a request
                # surfaced as "terminating connection due to administrator
                # command" on the first statement (integration resilience
                # drill, 2026-08-23). The check replaces dead connections
                # instead of serving them.
                check=ConnectionPool.check_connection,
            )
        if _POOL.closed:
            _POOL.open()
        return _POOL


def close_pool() -> None:
    global _POOL
    with _LOCK:
        if _POOL is not None and not _POOL.closed:
            _POOL.close()


def transaction(fn: t.Callable[[t.Any], t.Any]) -> t.Any:
    with pool().connection() as connection:
        with connection.transaction():
            return fn(connection)


atexit.register(close_pool)

"""Small durable idempotency ledger for externally retried webhook events.

This is deliberately not a durable work queue.  Slack owns retry delivery; the
ledger only prevents the same signed event from producing duplicate effects.
Each operation opens its own SQLite connection so worker threads never share a
connection object.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import time
from contextlib import closing


class EventLedger:
    def __init__(self, path: str | None = None, retention_seconds: int = 7 * 86400):
        default = pathlib.Path(".mari") / "operations.sqlite3"
        self.path = pathlib.Path(path or os.environ.get("MARI_OPERATIONS_DB", str(default)))
        self.retention_seconds = retention_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _ensure(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                completed_at REAL,
                PRIMARY KEY (provider, event_id)
            )""")

    def claim(self, provider: str, event_id: str) -> bool:
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM webhook_events WHERE claimed_at < ?",
                               (now - self.retention_seconds,))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO webhook_events(provider,event_id,claimed_at) VALUES (?,?,?)",
                (provider, event_id, now))
            connection.commit()
            return cursor.rowcount == 1

    def complete(self, provider: str, event_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE webhook_events SET completed_at=? WHERE provider=? AND event_id=?",
                (time.time(), provider, event_id))

    def release(self, provider: str, event_id: str) -> None:
        """Allow provider retry when work never entered the bounded executor."""
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM webhook_events WHERE provider=? AND event_id=? AND completed_at IS NULL",
                (provider, event_id))

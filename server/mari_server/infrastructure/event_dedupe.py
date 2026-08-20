"""PostgreSQL idempotency ledger for externally retried webhook events."""

from __future__ import annotations

import os

import psycopg


class EventLedger:
    def __init__(self, db_url: str | None = None, retention_seconds: int = 7 * 86400):
        if db_url is None:
            from mari_server.infrastructure.postgres import database_url
            db_url = database_url()
        self.db_url = db_url
        self.retention_seconds = retention_seconds

    def _connect(self):
        return psycopg.connect(self.db_url)

    def claim(self, provider: str, event_id: str) -> bool:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM webhook_events WHERE claimed_at < now() - (%s * interval '1 second')",
                (self.retention_seconds,),
            )
            row = connection.execute(
                """INSERT INTO webhook_events(provider, event_id, claimed_at)
                   VALUES (%s, %s, now()) ON CONFLICT DO NOTHING
                   RETURNING event_id""",
                (provider, event_id),
            ).fetchone()
            return row is not None

    def complete(self, provider: str, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE webhook_events SET completed_at=now()
                   WHERE provider=%s AND event_id=%s""",
                (provider, event_id),
            )

    def release(self, provider: str, event_id: str) -> None:
        """Allow provider retry when work never entered the bounded executor."""
        with self._connect() as connection:
            connection.execute(
                """DELETE FROM webhook_events
                   WHERE provider=%s AND event_id=%s AND completed_at IS NULL""",
                (provider, event_id),
            )

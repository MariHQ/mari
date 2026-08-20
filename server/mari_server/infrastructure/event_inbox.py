"""Durable, leased delivery inbox for provider webhooks.

HTTP handlers persist signed deliveries before acknowledging providers.  A
small dispatcher claims work with ``FOR UPDATE SKIP LOCKED`` so several API
processes may safely share the queue.  A coalesce key serializes related work
(for example all events for one Slack thread) without discarding deliveries.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import typing as t

import psycopg
from psycopg.rows import dict_row


Handler = t.Callable[[dict[str, t.Any]], None]
log = logging.getLogger("mari.event_inbox")


class EventInbox:
    def __init__(self, db_url: str | None = None, *, lease_seconds: int = 60,
                 max_attempts: int = 12):
        if db_url is None:
            from mari_server.infrastructure.postgres import database_url
            db_url = database_url()
        self.db_url = db_url
        self.lease_seconds = max(5, lease_seconds)
        self.max_attempts = max(1, max_attempts)

    def _connect(self):
        return psycopg.connect(self.db_url, row_factory=dict_row)

    def enqueue(self, provider: str, project_id: int, delivery_id: str,
                payload: dict[str, t.Any], *, coalesce_key: str = "") -> tuple[int, bool]:
        """Persist a delivery, returning ``(row_id, inserted)``.

        The uniqueness boundary includes the project because provider delivery
        identifiers are not guaranteed to be global across installations.
        """
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO event_inbox
                       (provider, project_id, delivery_id, payload, coalesce_key)
                     VALUES (%s, %s, %s, %s, %s)
                     ON CONFLICT (provider, project_id, delivery_id) DO NOTHING
                     RETURNING id""",
                (provider, project_id, delivery_id, json.dumps(payload), coalesce_key),
            ).fetchone()
            if row:
                return int(row["id"]), True
            existing = conn.execute(
                """SELECT id FROM event_inbox
                    WHERE provider=%s AND project_id=%s AND delivery_id=%s""",
                (provider, project_id, delivery_id),
            ).fetchone()
            return int(existing["id"]), False

    def claim(self) -> dict[str, t.Any] | None:
        """Lease one ready row, recovering work whose worker lease expired."""
        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """SELECT e.id
                         FROM event_inbox e
                        WHERE e.attempts < %s
                          AND e.available_at <= now()
                          AND (e.status = 'pending'
                               OR (e.status = 'processing' AND e.lease_until < now()))
                          AND NOT EXISTS (
                              SELECT 1 FROM event_inbox active
                               WHERE active.provider=e.provider
                                 AND active.project_id=e.project_id
                                 AND active.coalesce_key <> ''
                                 AND active.coalesce_key=e.coalesce_key
                                 AND active.status='processing'
                                 AND active.lease_until >= now()
                                 AND active.id <> e.id)
                        ORDER BY e.available_at, e.id
                        FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (self.max_attempts,),
                ).fetchone()
                if not row:
                    return None
                return conn.execute(
                    """UPDATE event_inbox
                          SET status='processing', attempts=attempts+1,
                              lease_until=now() + (%s * interval '1 second'),
                              updated_at=now()
                        WHERE id=%s RETURNING *""",
                    (self.lease_seconds, row["id"]),
                ).fetchone()

    def complete(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE event_inbox SET status='completed', completed_at=now(),
                          lease_until=NULL, last_error='', updated_at=now()
                    WHERE id=%s AND status='processing'""", (row_id,))

    def extend_lease(self, row_id: int) -> bool:
        """Keep a legitimately long provider drain from being reclaimed."""
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE event_inbox
                      SET lease_until=now() + (%s * interval '1 second'), updated_at=now()
                    WHERE id=%s AND status='processing' AND lease_until >= now()
                    RETURNING id""",
                (self.lease_seconds, row_id),
            ).fetchone()
            return row is not None

    def retry(self, row_id: int, error: str, attempts: int) -> None:
        terminal = attempts >= self.max_attempts
        delay = min(300, 2 ** min(max(attempts, 1), 8))
        with self._connect() as conn:
            conn.execute(
                """UPDATE event_inbox
                      SET status=%s, lease_until=NULL,
                          available_at=now() + (%s * interval '1 second'),
                          last_error=%s, updated_at=now()
                    WHERE id=%s""",
                ("dead" if terminal else "pending", delay, error[:1000], row_id),
            )


DEFAULT_INBOX = EventInbox()


class EventDispatcher:
    def __init__(self, inbox: EventInbox | t.Any, handlers: dict[str, Handler], *,
                 workers: int = 2, idle_seconds: float = 0.2):
        self.inbox = inbox
        self.handlers = handlers
        self.workers = max(1, workers)
        self.idle_seconds = idle_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            return
        self._stop.clear()
        self._threads = [threading.Thread(target=self._run, daemon=True,
                                         name=f"mari-event-{index}")
                         for index in range(self.workers)]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self._threads = []

    def drain_once(self) -> bool:
        row = self.inbox.claim()
        if not row:
            return False
        heartbeat_stop = threading.Event()
        heartbeat: threading.Thread | None = None
        if hasattr(self.inbox, "extend_lease"):
            def keep_lease() -> None:
                interval = max(1.0, float(getattr(self.inbox, "lease_seconds", 60)) / 3)
                while not heartbeat_stop.wait(interval):
                    try:
                        if not self.inbox.extend_lease(int(row["id"])):
                            return
                    except Exception:
                        log.exception("event lease heartbeat failed: id=%s", row["id"])
            heartbeat = threading.Thread(target=keep_lease, daemon=True,
                                         name=f"mari-event-lease-{row['id']}")
            heartbeat.start()
        try:
            handler = self.handlers.get(str(row["provider"]))
            if handler is None:
                raise RuntimeError(f"no event handler registered for {row['provider']}")
            handler(row)
        except Exception as exc:  # delivery remains durable and retryable
            log.exception("provider event failed: id=%s provider=%s", row["id"], row["provider"])
            self.inbox.retry(int(row["id"]), f"{type(exc).__name__}: {exc}",
                             int(row.get("attempts") or 1))
        else:
            self.inbox.complete(int(row["id"]))
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=1.0)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.drain_once():
                self._stop.wait(self.idle_seconds)

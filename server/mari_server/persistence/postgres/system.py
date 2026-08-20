"""Operational database probes and metrics read models."""

from mari_server.persistence.postgres import connection as db


def ready() -> None:
    with db.connect() as conn:
        conn.execute("SELECT 1 AS ok").fetchone()


def connector_lag() -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""SELECT provider, extract(epoch FROM (now() - last_sync_at)) AS lag
          FROM sources WHERE last_sync_at IS NOT NULL""").fetchall()

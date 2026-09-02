"""Operational database probes and metrics read models."""

from mari_server.persistence.postgres import connection as db


def ready() -> None:
    # Borrow from the request pool rather than dialing a fresh connection per
    # probe: kubelet and the load balancer poll /readyz every few seconds, and
    # each new socket costs a TLS handshake and a backend slot on the database.
    # The pool validates the connection at checkout, so a dead one after a
    # database restart is replaced here instead of failing the probe.
    with db.request_connection() as conn:
        conn.execute("SELECT 1 AS ok").fetchone()


def connector_lag() -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""SELECT provider, extract(epoch FROM (now() - last_sync_at)) AS lag
          FROM sources WHERE last_sync_at IS NOT NULL""").fetchall()

"""Operational database probes and metrics read models."""

from mari_server.persistence.postgres import connection as db


# How long the probe waits for a pooled connection. The chart's readiness
# probe gives the whole request 3 s; the pool's default wait is 30 s. Under
# load a probe that waits the default blocks past kubelet's timeout and the
# single replica goes unready for nothing.
READY_POOL_TIMEOUT_SECONDS = 1.0


def ready() -> None:
    # Borrow from the request pool rather than dialing a fresh connection per
    # probe: kubelet and the load balancer poll /readyz every few seconds, and
    # each new socket costs a TLS handshake and a backend slot on the database.
    # The pool validates the connection at checkout, so a dead one after a
    # database restart is replaced here instead of failing the probe.
    # A saturated pool raises psycopg_pool.PoolTimeout after the short wait
    # rather than hanging; the route turns that into a 503.
    with db.pool().connection(timeout=READY_POOL_TIMEOUT_SECONDS) as conn:
        conn.execute("SELECT 1 AS ok").fetchone()


def connector_lag() -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""SELECT provider, extract(epoch FROM (now() - last_sync_at)) AS lag
          FROM sources WHERE last_sync_at IS NOT NULL""").fetchall()

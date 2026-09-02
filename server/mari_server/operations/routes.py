"""Process health and metrics endpoints."""

from __future__ import annotations

import hmac
import logging
import typing as t

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from psycopg_pool import PoolTimeout

from mari_server import settings as config
from mari_server.operations import telemetry
from mari_server.persistence.postgres import system


router = APIRouter()


@router.get("/livez", include_in_schema=False)
def livez() -> dict[str, t.Any]:
    return {"ok": True, "service": "mari-api"}


@router.get("/readyz", include_in_schema=False)
def readyz(request: Request) -> dict[str, t.Any]:
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(503, "Application startup is not complete.")
    try:
        system.ready()
    except PoolTimeout as error:
        # Every pooled connection is busy serving requests. The database is
        # not down, so say so: the fix is capacity, not a restart.
        logging.getLogger("mari.health").warning(
            "database readiness check timed out waiting for a pooled connection",
        )
        raise HTTPException(503, "Database connection pool is saturated.") from error
    except Exception as error:
        logging.getLogger("mari.health").warning(
            "database readiness check failed", exc_info=error,
        )
        raise HTTPException(503, "Database is unavailable.") from error
    return {"ok": True, "service": "mari-api", "dependencies": {"database": "ok"}}


@router.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> dict[str, t.Any]:
    return readyz(request)


def _require_metrics_token(request: Request) -> None:
    """With no token configured /metrics stays open: on the k8s image nginx
    never proxies it and Prometheus scrapes the internal Service. The Lambda
    serves the API to the internet directly, so that stack sets
    MARI_METRICS_TOKEN and a scrape has to present it as a bearer."""
    expected = str(config.get("server", "metrics_token") or "").strip()
    if not expected:
        return
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(401, "A metrics bearer token is required.",
                            headers={"WWW-Authenticate": "Bearer"})


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics(request: Request) -> str:
    _require_metrics_token(request)
    try:
        for row in system.connector_lag():
            provider = str(row["provider"] or "unknown").split(":", 1)[0]
            telemetry.observe_connector_lag(provider, float(row["lag"] or 0))
    except Exception:
        telemetry.METRICS.inc("mari_metrics_dependency_errors_total", dependency="database")
    return telemetry.METRICS.render()

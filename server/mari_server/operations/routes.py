"""Process health and metrics endpoints."""

from __future__ import annotations

import logging
import typing as t

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

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
    except Exception as error:
        logging.getLogger("mari.health").warning(
            "database readiness check failed", exc_info=error,
        )
        raise HTTPException(503, "Database is unavailable.") from error
    return {"ok": True, "service": "mari-api", "dependencies": {"database": "ok"}}


@router.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> dict[str, t.Any]:
    return readyz(request)


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics() -> str:
    try:
        for row in system.connector_lag():
            provider = str(row["provider"] or "unknown").split(":", 1)[0]
            telemetry.observe_connector_lag(provider, float(row["lag"] or 0))
    except Exception:
        telemetry.METRICS.inc("mari_metrics_dependency_errors_total", dependency="database")
    return telemetry.METRICS.render()

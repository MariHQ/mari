"""Process composition and lifecycle ownership for the Mari server."""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mari_server import settings as config
from mari_server.destinations import slack
from mari_server.identity import routes as identity_routes
from mari_server.operations import telemetry
from mari_server.persistence.postgres import repository_audit
from mari_server.persistence.postgres.database import close_pool, ensure_schema, open_pool
from mari_server.providers import models
from mari_server.sources import gdrive_events, sync


@asynccontextmanager
async def lifespan(application: FastAPI):
    telemetry.configure_logging(os.environ.get("MARI_LOG_LEVEL", "INFO"))
    application.state.ready = False
    application.state.started_at = time.time()
    try:
        open_pool()
        ensure_schema()
        identity_routes.ensure_schema()
        repository_audit.ensure_schema()
        identity_routes.first_run_check()
        if os.environ.get("MARI_EMBEDDING_WARMUP", "").strip().lower() in {"1", "true", "yes"}:
            started = time.perf_counter()
            vector = models.embed("Mari search readiness")
            if vector is None:
                raise RuntimeError(f"embedding warmup failed: {models.last_error() or 'unknown error'}")
            logging.getLogger("mari.lifecycle").info(
                "embedding model ready", extra={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
        sync.start_poller()
        slack.start_event_dispatcher()
        native_ingestion = str(config.get("knowledge_substrate", "provider", "native")).lower() == "native"
        if native_ingestion:
            gdrive_events.start_watch_renewal()
        application.state.ready = True
        logging.getLogger("mari.lifecycle").info("application ready")
        yield
    finally:
        application.state.ready = False
        if str(config.get("knowledge_substrate", "provider", "native")).lower() == "native":
            gdrive_events.stop_watch_renewal()
        slack.stop_event_dispatcher()
        sync.stop_poller()
        close_pool()
        logging.getLogger("mari.lifecycle").info("application stopped")

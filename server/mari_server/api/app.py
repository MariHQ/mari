"""Mari API — FastAPI + Strawberry GraphQL over local Postgres.

Setup (idempotent):  createdb mari_cloud; psql mari_cloud -f init.sql
Backfill:            python backfill.py         (embeds documents via ollama)
Run:                 uvicorn app:app --reload --port 8000

DESIGN.md §4–§5: hybrid search = tsvector rank + pgvector cosine, boosted by
tag weights (tag_definitions.search_weight). LLM features run on local ollama
(gemma3:4b by default) through the configured model provider.

Module layout: db.py (helpers) · gqltypes.py (GraphQL types) · queries.py
(Query root + hybrid search) · mutations_knowledge/publish/admin.py (Mutation
root, merged below via inheritance). This file wires the app together.
"""

from __future__ import annotations

import logging
import os
import pathlib
import time
import typing as t
from contextlib import asynccontextmanager

import psycopg
import strawberry
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter

from mari_server import config
from mari_server.services import sync as ingest
from mari_server.integrations import llm
from mari_server.api import agent as agent_api
from mari_server.api import chat as chat_api
from mari_server.api.graphql_destinations import DestinationMutations
from mari_server.api.graphql_workflows import WorkflowMutations
from mari_server.api import access as access_module
from mari_server.api import auth as auth_module
from mari_server.api import bots
from mari_server.api import mcp
from mari_server.api import connectors as connectors_api
from mari_server.api import onboarding as onboard
from mari_server.services import repository_audit as repoaudit
from mari_server.api import provider_events
from mari_server import observability
from mari_server.api import enterprise_identity
from mari_server.api import gdrive_events

from mari_server.repositories.database import close_pool, ensure_schema, exec_, open_pool, q, q1
from mari_server.api.graphql_queries import Query
from mari_server.api.graphql_knowledge import MutKnowledge
from mari_server.api.graphql_admin import MutAdmin


@strawberry.type
class Mutation(MutKnowledge, WorkflowMutations, DestinationMutations, MutAdmin):
    pass


schema = strawberry.Schema(query=Query, mutation=Mutation)


def graphql_context(request: Request) -> dict[str, t.Any]:
    """The whole GraphQL surface requires a session. Resolvers read
    context['user']; `current_user` also publishes the caller for db.audit(),
    so a resolver that writes an event records who asked for it."""
    user = auth_module.current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required.")
    access = auth_module.require_project(request)
    return {"user": user, "access": access, "request": request}


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Own schema initialization and background services for this ASGI process."""
    observability.configure_logging(os.environ.get("MARI_LOG_LEVEL", "INFO"))
    application.state.ready = False
    application.state.started_at = time.time()
    try:
        open_pool()
        ensure_schema()
        auth_module.ensure_schema()
        repoaudit.ensure_schema()
        auth_module.first_run_check()
        ingest.start_poller()
        bots.start_event_dispatcher()
        gdrive_events.start_watch_renewal()
        application.state.ready = True
        logging.getLogger("mari.lifecycle").info("application ready")
        yield
    finally:
        application.state.ready = False
        gdrive_events.stop_watch_renewal()
        bots.stop_event_dispatcher()
        ingest.stop_poller()
        close_pool()
        logging.getLogger("mari.lifecycle").info("application stopped")


app = FastAPI(title="Mari API", lifespan=lifespan)
# Resolve who is calling once, for the whole request, so every write can record
# the real actor (AUTH-5) instead of a hardcoded name. Added before CORS so it
# sits INSIDE it (Starlette makes the last-added middleware outermost) — a CORS
# preflight never needs a session, and never gets a database query here.
app.add_middleware(auth_module.CallerMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.get("server", "cors_origins") or ["http://localhost:5173"]),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
# Added last so telemetry is the outermost layer: preflights, auth failures,
# and handler exceptions all receive correlation headers and contribute metrics.
app.add_middleware(observability.RequestTelemetryMiddleware)
_authed = [Depends(auth_module.require_project)]
app.include_router(GraphQLRouter(schema, context_getter=graphql_context), prefix="/graphql")
app.include_router(auth_module.router)
app.include_router(enterprise_identity.router)
app.include_router(bots.router)  # setup endpoints guard themselves; webhooks stay signature-verified
app.include_router(gdrive_events.router)
app.include_router(provider_events.router)
app.include_router(mcp.router)  # published MCP servers authenticate with their own bearer tokens
app.include_router(connectors_api.router, dependencies=_authed)
app.include_router(agent_api.router, dependencies=_authed)
app.include_router(chat_api.router)
app.include_router(onboard.router, dependencies=_authed)

from pydantic import BaseModel

class ApiSearchIn(BaseModel):
    query: str
    limit: int = 10


@app.post("/api/search", include_in_schema=True)
def api_search(body: ApiSearchIn, authorization: str = Header(default="")) -> dict[str, t.Any]:
    """Project-scoped search target for enterprise gateways and assistants."""
    import hashlib

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Bearer API key required.")
    row = q1("""SELECT k.id, k.project_id, k.scopes, p.slug, p.name AS project_name
                  FROM api_keys k JOIN projects p ON p.id = k.project_id
                 WHERE k.token_hash = %s AND NOT k.revoked AND p.status = 'active'""",
             (hashlib.sha256(token.encode()).hexdigest(),))
    if not row:
        raise HTTPException(401, "Invalid or revoked API key.")
    scopes = {value.strip() for value in str(row["scopes"] or "").split(",")}
    if not ({"read", "search"} & scopes):
        raise HTTPException(403, "This API key does not allow search.")
    ctx = access_module.external_access(
        row["project_id"], row["slug"], row["project_name"], "api_key", str(row["id"]),
        frozenset({"knowledge.read"}),
    )
    with access_module.use_access(ctx):
        rows = hybrid_search(body.query.strip(), max(1, min(body.limit, 50)))
    exec_("UPDATE api_keys SET last_used = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s",
          (row["id"],))
    return {"results": [{"id": item["id"], "title": item["title"],
                          "source": item["source"], "snippet": item["snippet"],
                          "score": item.get("score", 0)} for item in rows]}


@app.get("/livez", include_in_schema=False)
def livez() -> dict[str, t.Any]:
    return {"ok": True, "service": "mari-api"}


@app.get("/readyz", include_in_schema=False)
def readyz(request: Request) -> dict[str, t.Any]:
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(503, "Application startup is not complete.")
    try:
        q1("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001 — readiness reports dependency failure
        logging.getLogger("mari.health").warning("database readiness check failed", exc_info=exc)
        raise HTTPException(503, "Database is unavailable.") from exc
    return {"ok": True, "service": "mari-api", "dependencies": {"database": "ok"}}


@app.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> dict[str, t.Any]:
    """Compatibility alias for orchestration that predates /readyz."""
    return readyz(request)


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics() -> str:
    # Connector lag is a gauge, refreshed on scrape so idle connectors still age.
    try:
        for row in q("""SELECT provider, extract(epoch FROM (now() - last_sync_at)) AS lag
                        FROM sources WHERE last_sync_at IS NOT NULL"""):
            provider = str(row["provider"] or "unknown").split(":", 1)[0]
            observability.observe_connector_lag(provider, float(row["lag"] or 0))
    except Exception:  # noqa: BLE001 — metrics remain available during DB incidents
        observability.METRICS.inc("mari_metrics_dependency_errors_total", dependency="database")
    return observability.METRICS.render()


# In the Lambda container the API also serves the compiled React application.
# Keep this catch-all last so /graphql, /auth, /chat, /sites, and /healthz win.
# Only register it when MARI_STATIC_DIR is actually set and exists — an empty
# env var used to resolve to Path(".") and serve the server's own CWD.
_static_env = os.environ.get("MARI_STATIC_DIR", "").strip()
STATIC_DIR = pathlib.Path(_static_env) if _static_env else None
if STATIC_DIR is not None and STATIC_DIR.is_dir():
    # The console ships its own brand fonts (web/public/fonts). Older Python
    # mimetypes tables have no entry for .woff2 and FileResponse would then
    # send them as application/octet-stream; browsers still render, but the
    # preload hint in index.html is typed font/woff2 and would be discarded.
    import mimetypes  # noqa: E402
    mimetypes.add_type("font/woff2", ".woff2")

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str):
        candidate = (STATIC_DIR / path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            candidate = STATIC_DIR / "index.html"
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

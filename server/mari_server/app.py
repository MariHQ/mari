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

import os
import pathlib
import typing as t

import strawberry
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter

from mari_server import settings as config
from mari_server.conversations import routes as agent_api
from mari_server.destinations import chat as chat_api
from mari_server.destinations.graphql import DestinationMutations
from mari_server.automations.graphql import WorkflowMutations
from mari_server.identity import access as access_module
from mari_server.identity import routes as auth_module
from mari_server.destinations import slack as bots
from mari_server.destinations import mcp
from mari_server.sources import routes as connectors_api
from mari_server.knowledge import onboarding as onboard
from mari_server.sources import provider_events
from mari_server.operations import telemetry as observability
from mari_server.identity import enterprise
from mari_server.sources import gdrive_events

from mari_server.bootstrap import lifespan
from mari_server.operations import routes as operation_routes
from mari_server.product.queries import Query
from mari_server.knowledge.graphql import MutKnowledge
from mari_server.identity.graphql import MutAdmin
from mari_server.search import routes as search_routes


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
app.include_router(enterprise.router)
app.include_router(bots.router)  # setup endpoints guard themselves; webhooks stay signature-verified
app.include_router(gdrive_events.router)
app.include_router(provider_events.router)
app.include_router(mcp.router)  # published MCP servers authenticate with their own bearer tokens
app.include_router(connectors_api.router, dependencies=_authed)
app.include_router(agent_api.router, dependencies=_authed)
app.include_router(chat_api.router)
app.include_router(onboard.router, dependencies=_authed)
app.include_router(search_routes.router)
app.include_router(operation_routes.router)


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

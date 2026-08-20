"""Mari API — FastAPI + Strawberry GraphQL over local Postgres.

Setup (idempotent):  createdb mari_cloud; psql mari_cloud -f init.sql
Backfill:            python backfill.py         (embeds documents via ollama)
Run:                 uvicorn app:app --reload --port 8000

DESIGN.md §4–§5: hybrid search = tsvector rank + pgvector cosine, boosted by
tag weights (tag_definitions.search_weight). LLM features run on local ollama
(gemma3:4b) with deterministic fallbacks so the demo works offline.

Module layout: db.py (helpers) · gqltypes.py (GraphQL types) · queries.py
(Query root + hybrid search) · mutations_knowledge/publish/admin.py (Mutation
root, merged below via inheritance). This file wires the app together.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
import typing as t
from contextlib import asynccontextmanager

import psycopg
import strawberry
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from strawberry.fastapi import GraphQLRouter

import config
import ingest
import llm
import sitebuilder
import agentchat
import access as access_module
import auth as auth_module
import bots
import mcp
import connectors_api
import onboard
import repoaudit
import provider_events
import observability
import enterprise_identity
import gdrive_events
from sitefiles import PublishedSiteFiles

from db import DB_URL, close_pool, ensure_schema, exec_, open_pool, q, q1
from queries import Query, hybrid_search, like_pattern
from mutations_knowledge import MutKnowledge
from mutations_publish import MutPublish
from mutations_admin import MutAdmin


@strawberry.type
class Mutation(MutKnowledge, MutPublish, MutAdmin):
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
app.include_router(agentchat.router, dependencies=_authed)
app.include_router(onboard.router, dependencies=_authed)

sitebuilder.BUILDS.mkdir(exist_ok=True)


def _site_preview_authenticated(scope: dict[str, t.Any], site: dict[str, t.Any]) -> bool:
    try:
        request = Request(scope)
        if auth_module.current_user(request) is None:
            return False
        return auth_module.require_project(request).project_id == int(site.get("project_id") or 0)
    except Exception:
        return False


app.mount(
    "/sites",
    PublishedSiteFiles(
        directory=str(sitebuilder.BUILDS),
        lookup=lambda site_id: q1("SELECT status, project_id FROM sites WHERE id = %s", (site_id,)),
        authenticated=_site_preview_authenticated,
    ),
    name="sites",
)


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


# ————————————————— chat (SSE streaming, DESIGN.md §10) —————————————————


class ChatIn(BaseModel):
    session_id: int | None = None
    message: str


def _log_chat_usage(detail: str) -> None:
    """Honest telemetry (BOTS-CONTRACT §A): one chat_answer per completed turn.
    Guarded — db.log_usage is added by another agent; never crash if absent."""
    try:
        import db as _db
        if hasattr(_db, "log_usage"):
            _db.log_usage("chat_answer", detail)
        else:
            exec_("INSERT INTO usage_log (kind, detail) VALUES (%s, %s)", ("chat_answer", detail))
    except Exception:
        pass


CHAT_SYSTEM = (
    "You are Mari, the team's knowledge assistant. Answer from the "
    "provided context. Be concise (2-4 sentences), cite sources as [1], [2]. If the "
    "context doesn't cover it, say so."
)


@app.post("/chat")
def chat(body: ChatIn, access: t.Any = Depends(auth_module.require_project)):
    project_id = access.project_id
    session_id = body.session_id
    if not session_id:
        with psycopg.connect(DB_URL) as conn:
            session_id = conn.execute(
                """INSERT INTO chat_sessions (project_id, owner_user_id, title)
                   VALUES (%s, %s, %s) RETURNING id""",
                (project_id, access.user_id or None, body.message[:60])
            ).fetchone()[0]
    else:
        owned = q1("""SELECT id FROM chat_sessions WHERE id = %s AND project_id = %s
                      AND (owner_user_id = %s OR owner_user_id IS NULL)""",
                   (session_id, project_id, access.user_id))
        if not owned:
            raise HTTPException(404, "Chat session not found.")
    exec_("""INSERT INTO chat_messages (project_id, session_id, role, content)
             VALUES (%s, %s, 'user', %s)""", (project_id, session_id, body.message))

    # Approved answers first (DESIGN.md canon: curated answers beat generation).
    qvec = llm.embed(body.message)
    approved = None
    if qvec:
        approved = q1("""
          SELECT id, question, answer, 1 - (embedding <=> %s::vector) AS sim
          FROM approved_answers
          WHERE project_id = %s AND status = 'approved' AND embedding IS NOT NULL
          ORDER BY embedding <=> %s::vector LIMIT 1
        """, (str(qvec), project_id, str(qvec)))
        if approved and approved["sim"] < 0.62:
            approved = None
    if not approved:
        hit = q1("""SELECT id, question, answer, 1.0 AS sim FROM approved_answers
                    WHERE project_id = %s AND status = 'approved'
                      AND (question ILIKE %s OR position(lower(question) in lower(%s)) > 0)
                    LIMIT 1""", (project_id, like_pattern(body.message[:60]), body.message))
        approved = hit

    if approved:
        exec_("""UPDATE approved_answers SET served = served + 1
                 WHERE id = %s AND project_id = %s""", (approved["id"], project_id))
        sources = [{"n": 1, "source": "approved", "title": approved["question"],
                    "meta": "Approved answer · served verbatim", "href": f"/answers?answer={approved['id']}"}]
        text = approved["answer"]

        def stream_approved():
            yield f"event: meta\ndata: {json.dumps({'session_id': session_id, 'sources': sources, 'approved': True})}\n\n"
            yield f"data: {json.dumps({'token': text})}\n\n"
            exec_("""INSERT INTO chat_messages (project_id, session_id, role, content, sources)
                     VALUES (%s, %s, 'assistant', %s, %s)""",
                  (project_id, session_id, text, json.dumps(sources)))
            _log_chat_usage("web")
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(stream_approved(), media_type="text/event-stream")

    # FastAPI executes this synchronous route and its dependency in separate
    # worker calls. The AccessContext object is therefore passed explicitly,
    # but ContextVar-based retrieval helpers need it installed in this call's
    # execution context as well.
    with access_module.use_access(access):
        docs = hybrid_search(body.message, 4)
    context = "\n\n".join(
        f"[{i + 1}] {d['title']} ({d['source']})\n{d['body'] or d['snippet']}" for i, d in enumerate(docs))
    facts = q("SELECT claim FROM facts WHERE project_id = %s AND status = 'Verified' LIMIT 8", (project_id,))
    context += "\n\nVerified facts:\n" + "\n".join(f"- {f['claim']}" for f in facts)
    sources = [{"n": i + 1, "source": d["source"], "title": d["title"],
                "meta": d["snippet"][:110], "document_id": d["id"],
                "href": f"/knowledge/doc?id={d['id']}"} for i, d in enumerate(docs)]

    history = q("""SELECT role, content FROM chat_messages
                   WHERE project_id = %s AND session_id = %s ORDER BY id DESC LIMIT 10""",
                (project_id, session_id))
    messages = [{"role": m["role"], "content": m["content"]} for m in reversed(history)]
    messages[-1]["content"] = f"Context:\n{context}\n\nQuestion: {body.message}"

    def stream():
        yield f"event: meta\ndata: {json.dumps({'session_id': session_id, 'sources': sources})}\n\n"
        answer = []
        for token in llm.chat_stream(messages, CHAT_SYSTEM):
            answer.append(token)
            yield f"data: {json.dumps({'token': token})}\n\n"
        if not answer:
            fallback = ("I couldn't reach the local model, but hybrid search found: "
                        + "; ".join(d["title"] for d in docs) + ".")
            answer.append(fallback)
            yield f"data: {json.dumps({'token': fallback})}\n\n"
        exec_("""INSERT INTO chat_messages (project_id, session_id, role, content, sources)
                 VALUES (%s, %s, 'assistant', %s, %s)""",
              (project_id, session_id, "".join(answer), json.dumps(sources)))
        _log_chat_usage("web")
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


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


@app.get("/knowledge-chat-api/{project_slug}/{destination_slug}", response_class=JSONResponse)
def knowledge_chat_destination(project_slug: str, destination_slug: str, request: Request) -> dict[str, t.Any]:
    """Configuration for a deployed, project-scoped interactive destination.

    The destination UI intentionally calls the existing authenticated `/chat`
    endpoint for answers; this endpoint only proves that the named destination
    is live and that the current member can read its project.
    """
    user = auth_module.current_user(request)
    if not user:
        raise HTTPException(401, "Authentication required.")
    project, _ = access_module.resolve_access(user, project_slug, auth_module._conn)
    if project is None:
        raise HTTPException(403, "You do not have access to that project.")
    row = q1("""SELECT name, slug, title, welcome
                FROM knowledge_chat_destinations
                WHERE project_id = %s AND slug = %s AND status = 'live'""",
             (project.project_id, destination_slug))
    if not row:
        raise HTTPException(404, "Knowledge chat destination not found.")
    return {"name": row["name"], "slug": row["slug"], "title": row["title"],
            "welcome": row["welcome"], "project": project_slug}


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

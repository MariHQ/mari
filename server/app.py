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
import os
import pathlib
import typing as t

import psycopg
import strawberry
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from strawberry.fastapi import GraphQLRouter

import config
import ingest
import llm
import sitebuilder
import agentchat
import auth as auth_module
import bots
import connectors_api
import onboard
import repoaudit

from db import DB_URL, ensure_schema, exec_, q, q1
from queries import Query, hybrid_search
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
    return {"user": user, "request": request}


app = FastAPI(title="Mari API")
# Resolve who is calling once, for the whole request, so every write can record
# the real actor (AUTH-5) instead of a hardcoded name. Added before CORS so it
# sits INSIDE it (Starlette makes the last-added middleware outermost) — a CORS
# preflight never needs a session, and never gets a database query here.
app.add_middleware(auth_module.CallerMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
_authed = [Depends(auth_module.require_user)]
app.include_router(GraphQLRouter(schema, context_getter=graphql_context), prefix="/graphql")
app.include_router(auth_module.router)
app.include_router(bots.router)  # setup endpoints guard themselves; webhooks stay signature-verified
app.include_router(connectors_api.router, dependencies=_authed)
app.include_router(agentchat.router, dependencies=_authed)
app.include_router(onboard.router, dependencies=_authed)
ensure_schema()  # complete app schema + demo seed (init.sql, idempotent)
auth_module.ensure_schema()
repoaudit.ensure_schema()
auth_module.first_run_check()
ingest.start_poller()

from fastapi.staticfiles import StaticFiles  # noqa: E402

sitebuilder.BUILDS.mkdir(exist_ok=True)
app.mount("/sites", StaticFiles(directory=str(sitebuilder.BUILDS), html=True), name="sites")


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
def chat(body: ChatIn, user: dict = Depends(auth_module.require_user)):
    session_id = body.session_id
    if not session_id:
        with psycopg.connect(DB_URL) as conn:
            session_id = conn.execute(
                "INSERT INTO chat_sessions (title) VALUES (%s) RETURNING id",
                (body.message[:60],)
            ).fetchone()[0]
    exec_("INSERT INTO chat_messages (session_id, role, content) VALUES (%s, 'user', %s)", (session_id, body.message))

    # Approved answers first (DESIGN.md canon: curated answers beat generation).
    qvec = llm.embed(body.message)
    approved = None
    if qvec:
        approved = q1("""
          SELECT id, question, answer, 1 - (embedding <=> %s::vector) AS sim
          FROM approved_answers
          WHERE status = 'approved' AND embedding IS NOT NULL
          ORDER BY embedding <=> %s::vector LIMIT 1
        """, (str(qvec), str(qvec)))
        if approved and approved["sim"] < 0.62:
            approved = None
    if not approved:
        hit = q1("""SELECT id, question, answer, 1.0 AS sim FROM approved_answers
                    WHERE status = 'approved' AND (question ILIKE %s OR %s ILIKE '%%' || question || '%%')
                    LIMIT 1""", (f"%{body.message[:60]}%", body.message))
        approved = hit

    if approved:
        exec_("UPDATE approved_answers SET served = served + 1 WHERE id = %s", (approved["id"],))
        sources = [{"n": 1, "source": "approved", "title": approved["question"],
                    "meta": "Approved answer · served verbatim"}]
        text = approved["answer"]

        def stream_approved():
            yield f"event: meta\ndata: {json.dumps({'session_id': session_id, 'sources': sources, 'approved': True})}\n\n"
            yield f"data: {json.dumps({'token': text})}\n\n"
            exec_("INSERT INTO chat_messages (session_id, role, content, sources) VALUES (%s, 'assistant', %s, %s)",
                  (session_id, text, json.dumps(sources)))
            _log_chat_usage("web")
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(stream_approved(), media_type="text/event-stream")

    docs = hybrid_search(body.message, 4)
    context = "\n\n".join(
        f"[{i + 1}] {d['title']} ({d['source']})\n{d['body'] or d['snippet']}" for i, d in enumerate(docs))
    facts = q("SELECT claim FROM facts WHERE status = 'Verified' LIMIT 8")
    context += "\n\nVerified facts:\n" + "\n".join(f"- {f['claim']}" for f in facts)
    sources = [{"n": i + 1, "source": d["source"], "title": d["title"],
                "meta": d["snippet"][:110]} for i, d in enumerate(docs)]

    history = q("SELECT role, content FROM chat_messages WHERE session_id = %s ORDER BY id DESC LIMIT 10", (session_id,))
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
        exec_("INSERT INTO chat_messages (session_id, role, content, sources) VALUES (%s, 'assistant', %s, %s)",
              (session_id, "".join(answer), json.dumps(sources)))
        _log_chat_usage("web")
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ————————————————— GitHub webhook (GITHUB-SYNC-CONTRACT.md) —————————————————


@app.post("/webhooks/github")
async def github_webhook(request: "Request"):
    import hashlib
    import hmac

    raw = await request.body()
    # Either the env/config secret or the self-serve settings.github_bot secret
    # validates (BOTS-CONTRACT §B — the UI saves its own generated secret).
    secrets = [
        s for s in (
            (config.get("github", "webhook_secret") or "").strip(),
            (bots.get_setting("github_bot").get("webhook_secret") or "").strip(),
        ) if s
    ]
    # AUTH-10: no secret used to mean no verification, which made this an
    # unauthenticated endpoint anyone could use to drive syncs. No secret now
    # means no deliveries — the same verdict bots.py gives Slack.
    if not secrets:
        raise HTTPException(
            401, "This webhook has no secret configured, so no delivery can be verified. "
                 "Set MARI_GITHUB_WEBHOOK_SECRET, or generate one in Settings → Bots.")
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not any(
        hmac.compare_digest(sig, "sha256=" + hmac.new(s.encode(), raw, hashlib.sha256).hexdigest())
        for s in secrets
    ):
        # A loud 401 so GitHub's delivery log surfaces the misconfiguration.
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad payload"}
    bots.merge_setting("github_bot", {"last_delivery_at": bots._now_iso()})
    full_name = (payload.get("repository") or {}).get("full_name", "")
    if not full_name:
        return {"ok": True, "synced": False}
    src = q1("SELECT id FROM sources WHERE kind = 'github' AND config->>'repo' = %s", (full_name,))
    if not src:
        return {"ok": True, "synced": False, "reason": "repo not connected"}
    started = ingest.start_sync(src["id"])
    return {"ok": True, "synced": started, "source_id": src["id"]}


@app.get("/healthz")
def healthz() -> dict[str, t.Any]:
    docs = q("SELECT count(*) AS n FROM documents")[0]["n"]
    embedded = q("SELECT count(*) AS n FROM documents WHERE embedding IS NOT NULL")[0]["n"]
    return {"ok": True, "service": "mari-cloud-api", "documents": docs, "embedded": embedded}


# In the Lambda container the API also serves the compiled React application.
# Keep this catch-all last so /graphql, /auth, /chat, /sites, and /healthz win.
# Only register it when MARI_STATIC_DIR is actually set and exists — an empty
# env var used to resolve to Path(".") and serve the server's own CWD.
_static_env = os.environ.get("MARI_STATIC_DIR", "").strip()
STATIC_DIR = pathlib.Path(_static_env) if _static_env else None
if STATIC_DIR is not None and STATIC_DIR.is_dir():
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

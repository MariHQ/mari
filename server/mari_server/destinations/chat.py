"""HTTP/SSE adapters for private and published knowledge chat."""

from __future__ import annotations

import json
import typing as t

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from mari_server import settings as config
from mari_server.identity import access
from mari_server.identity import routes as auth
from mari_components.destinations.chat import stream_answer
from mari_server.conversations.chat import answers_since, live_destination, ports


router = APIRouter()


class ChatIn(BaseModel):
    # An int for the signed-in dock; a public visitor echoes back the
    # "<id>.<token>" reference the meta event gave them (conversations/chat.py).
    session_id: int | str | None = None
    message: str


def _sse(project_access: access.AccessContext, body: ChatIn, usage: str,
         enabled_tools: frozenset[str], surface: str = "dock"):
    try:
        events = stream_answer(body.session_id, body.message,
                               ports=ports(project_access, usage, enabled_tools, surface))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error

    def response():
        iterator = iter(events)
        while True:
            with access.use_access(project_access):
                try:
                    event = next(iterator)
                except StopIteration:
                    return
            yield f"event: {event.kind}\ndata: {json.dumps(dict(event.payload))}\n\n"
    return StreamingResponse(response(), media_type="text/event-stream")


@router.post("/chat")
def private_chat(body: ChatIn, project_access: access.AccessContext = Depends(access.require_project)):
    return _sse(project_access, body, "web", frozenset({"search", "facts", "answers"}))


def _live(project_slug: str, destination_slug: str) -> dict[str, t.Any]:
    row = live_destination(project_slug, destination_slug)
    if not row:
        raise HTTPException(404, "Knowledge chat destination not found.")
    return row


@router.get("/knowledge-chat-api/{project_slug}/{destination_slug}", response_class=JSONResponse)
def destination(project_slug: str, destination_slug: str) -> dict[str, t.Any]:
    row = _live(project_slug, destination_slug)
    return {"name": row["name"], "slug": row["slug"], "title": row["title"],
            "welcome": row["welcome"], "project": project_slug}


def _throttle(request: Request, project_id: int, usage_detail: str) -> None:
    """Brakes on the one chat surface that needs no sign-in, since every call
    drives retrieval and the model. The two windows reuse the sign-in
    limiter, so they are per process; the daily budget reads the usage_log
    rows every instance writes, so it holds for the whole fleet. Each limit
    comes from settings and 0 switches it off."""
    per_ip = int(config.get("knowledge_chat", "ip_per_minute") or 0)
    per_destination = int(config.get("knowledge_chat", "destination_per_minute") or 0)
    daily = int(config.get("knowledge_chat", "daily_budget") or 0)
    if per_ip > 0:
        auth._rate_limit("knowledge-chat-ip", auth._client_ip(request), per_ip, 60)
    if per_destination > 0:
        auth._rate_limit("knowledge-chat-destination", usage_detail, per_destination, 60)
    if daily > 0 and answers_since(project_id, usage_detail, 24) >= daily:
        raise HTTPException(
            429, "This knowledge chat has reached its daily limit. Try again later.",
            headers={"Retry-After": "3600"})


@router.post("/knowledge-chat-api/{project_slug}/{destination_slug}/chat")
def public_chat(project_slug: str, destination_slug: str, body: ChatIn, request: Request):
    row = _live(project_slug, destination_slug)
    usage_detail = f"knowledge_chat:{row['id']}"
    _throttle(request, int(row["project_id"]), usage_detail)
    project_access = access.external_access(
        row["project_id"], row["project_slug"], row["project_name"],
        "knowledge_chat", str(row["id"]), frozenset({"knowledge.read"}),
    )
    configured = row.get("tools") or []
    if isinstance(configured, str):
        configured = json.loads(configured)
    # A published destination is read by people outside the workspace, so it
    # gets the public surface rules, not the dock's.
    return _sse(project_access, body, usage_detail,
                frozenset(str(tool) for tool in configured), "public")

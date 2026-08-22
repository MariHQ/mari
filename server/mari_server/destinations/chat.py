"""HTTP/SSE adapters for private and published knowledge chat."""

from __future__ import annotations

import json
import typing as t

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from mari_server.identity import access
from mari_components.destinations.chat import stream_answer
from mari_server.conversations.chat import live_destination, ports


router = APIRouter()


class ChatIn(BaseModel):
    session_id: int | None = None
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


@router.post("/knowledge-chat-api/{project_slug}/{destination_slug}/chat")
def public_chat(project_slug: str, destination_slug: str, body: ChatIn):
    row = _live(project_slug, destination_slug)
    project_access = access.external_access(
        row["project_id"], row["project_slug"], row["project_name"],
        "knowledge_chat", str(row["id"]), frozenset({"knowledge.read"}),
    )
    configured = row.get("tools") or []
    if isinstance(configured, str):
        configured = json.loads(configured)
    # A published destination is read by people outside the workspace, so it
    # gets the public surface rules, not the dock's.
    return _sse(project_access, body, f"knowledge_chat:{row['id']}",
                frozenset(str(tool) for tool in configured), "public")

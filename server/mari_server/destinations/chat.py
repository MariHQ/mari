"""HTTP/SSE adapters for private and published knowledge chat."""

from __future__ import annotations

import json
import re
import typing as t

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from mari_server import settings as config
from mari_server.identity import access
from mari_server.identity import routes as auth
from mari_components.destinations.chat import ChatEvent, stream_answer
from mari_server.conversations import citations
from mari_server.conversations.chat import MODEL_UNAVAILABLE, answers_since, live_destination, ports


router = APIRouter()


class ChatIn(BaseModel):
    # An int for the signed-in dock; a public visitor echoes back the
    # "<id>.<token>" reference the meta event gave them (conversations/chat.py).
    session_id: int | str | None = None
    message: str


# How much of an ambiguous opening is held back before it streams as-is: long
# enough to cover a fenced not-found sentence, short enough that a real code
# answer starts appearing within its first line or two.
HOLD_LIMIT = 240

# A closed fence, or a closed inline span, with anything at all after it: the
# opening can no longer be a wrapper around the whole not-found sentence, so
# there is nothing left to wait for.
_CLOSED_CODE = re.compile(
    r"^(`{3,})[^\n]*\n[\s\S]*?\n[ \t]*\1[ \t]*[\s\S]"
    r"|^`[^`\n]+`[\s\S]"
)


def _ambiguous(text: str) -> bool:
    """Whether the tokens so far could still be a wrapped not-found sentence:
    nothing but whitespace, or an opening backtick whose fence or span has not
    closed with more text behind it. A tilde is not a trigger: "~30 days" is an
    estimate that must stream at once, and a tilde fence (which models do not
    write around prose) is unwrapped in the transcript rather than held for."""
    lead = text.lstrip()
    if not lead:
        return len(text) < HOLD_LIMIT
    if lead[0] == "`":
        return len(lead) < HOLD_LIMIT and not _CLOSED_CODE.search(lead)
    return False


def narrowed(events: t.Iterable[ChatEvent]) -> t.Iterator[ChatEvent]:
    """The library's event stream, with the answer cleaned for a Markdown
    renderer and the sources settled once the answer is known.

    `meta` still carries every retrieved document, so a client can link [3]
    from the first token. But it is not the last word: after the answer, a
    `sources` event carries only the rows the answer cites, and none for a
    "could not find" answer, and a client shows that instead. The first tokens
    are held back only while they are ambiguous (whitespace, or a fence that
    may be wrapping the not-found sentence), so a plain answer streams as
    before. A stream that was nothing but whitespace reads as the library's
    own "model unavailable" turn. Text still held when the model fails is
    flushed before the failure reaches the client, so nothing the model said
    is lost.
    """
    candidates: list = []
    parts: list[str] = []
    held: list[str] = []
    holding = True
    started = False
    try:
        for event in events:
            if event.kind == "meta":
                candidates = list(event.payload.get("sources") or [])
                yield event
            elif event.kind == "token":
                text = str(event.payload.get("token") or "")
                parts.append(text)
                if holding:
                    held.append(text)
                    if _ambiguous("".join(held)):
                        continue
                    holding = False
                    text = citations.clean_answer("".join(held))
                elif not started:
                    # The hold ran out on whitespace alone; keep trimming
                    # until the answer starts, so no indent reaches the
                    # renderer.
                    text = text.lstrip()
                if text:
                    started = True
                    yield ChatEvent("token", {"token": text})
            elif event.kind == "done":
                if holding:
                    text = citations.clean_answer("".join(held))
                    if text:
                        started = True
                        yield ChatEvent("token", {"token": text})
                answer = citations.clean_answer("".join(parts))
                if not answer:
                    yield ChatEvent("token", {"token": MODEL_UNAVAILABLE})
                if answer and answer != MODEL_UNAVAILABLE:
                    yield ChatEvent("sources", {"sources": citations.cited(answer, candidates)})
                else:
                    # A warning cites nothing, whichever side said it.
                    yield ChatEvent("sources", {"sources": []})
                yield event
            else:
                yield event
    except Exception:
        # GeneratorExit is deliberately not caught: a closed generator must
        # not yield.
        if holding and held:
            text = citations.clean_answer("".join(held))
            if text:
                yield ChatEvent("token", {"token": text})
        raise


def _sse(project_access: access.AccessContext, body: ChatIn, usage: str,
         enabled_tools: frozenset[str], surface: str = "dock"):
    try:
        events = narrowed(stream_answer(
            body.session_id, body.message,
            ports=ports(project_access, usage, enabled_tools, surface)))
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

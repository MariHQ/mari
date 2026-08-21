"""FastAPI/SSE adapter for the streaming agent use case."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mari_server.identity import access
from mari_components.agents.runtime import AgentOutput, stream_agent_turn
from mari_server.conversations.agent import production_runtime


router = APIRouter()


class AgentChatIn(BaseModel):
    session_id: int | None = None
    message: str


def serialize_sse(outputs: Iterable[AgentOutput]) -> Iterator[str]:
    for output in outputs:
        yield f"event: {output.kind}\ndata: {json.dumps(dict(output.payload))}\n\n"


@router.post("/agent/chat")
def agent_chat(
    body: AgentChatIn,
    project_access: access.AccessContext = Depends(access.require_project),
):
    message = body.message.strip()[:8000]
    runtime = production_runtime(project_access)
    session_id = body.session_id
    if session_id is None:
        session_id = runtime.create_session(message or "Agent chat")
    else:
        try:
            runtime.require_session(session_id)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
    runtime.append_user_message(session_id, message)
    bindings = runtime.bindings()
    outputs = stream_agent_turn(
        session_id, message, bindings, runtime.ports(bindings),
        minimum_tool_observations=1,
    )

    def response() -> Iterator[str]:
        yield f"event: meta\ndata: {json.dumps({'session_id': session_id})}\n\n"
        iterator = iter(outputs)
        while True:
            # StreamingResponse may resume a synchronous generator in a
            # different worker context after every yield. Set and reset the
            # ContextVar around next(), never across the yield boundary.
            with access.use_access(project_access):
                try:
                    output = next(iterator)
                except StopIteration:
                    return
            yield f"event: {output.kind}\ndata: {json.dumps(dict(output.payload))}\n\n"

    return StreamingResponse(response(), media_type="text/event-stream")

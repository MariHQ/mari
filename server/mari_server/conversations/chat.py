"""Product adapters for the streaming knowledge-answer use case."""

from __future__ import annotations

import json
import re
import secrets

from mari_server.identity import context as access
from mari_server.providers import models as llm
from mari_server.persistence.postgres.database import log_usage
from mari_server.persistence.postgres import chat as chat_store
from mari_server.persistence.postgres import documents as document_store
from mari_server.persistence.postgres import trajectories as trajectory_store
from mari_server.search.service import hybrid_search, slack_channel_search
from mari_server.conversations import citations
from mari_server.conversations.prompts import answer_system, workspace_style_text
from mari_components.destinations.chat import ChatContext, ChatPorts, answer_search_query
from mari_server.conversations.workflows import guidance as workflow_guidance
from mari_server.conversations.workflows import retrieval_query as workflow_retrieval_query
from mari_server.conversations.workflows import select as select_workflow
from mari_server.conversations.workflows import cached_response as workflow_cached_response


# The library's own words for a turn that produced no answer
# (mari_components.destinations.chat.stream_answer). The library says them
# when the model sent nothing at all; the server says them again when the
# model sent nothing but whitespace, so both turns read and persist the same.
MODEL_UNAVAILABLE = "The configured language model is unavailable. Check model settings and try again."

_SLACK_CHANNEL = re.compile(
    r"(?:in|from)\s+(?:the\s+)?#?([a-z0-9][a-z0-9_-]*)\s+channel\b",
    re.IGNORECASE,
)


def requested_slack_channel(question: str) -> str | None:
    match = _SLACK_CHANNEL.search(question)
    return match.group(1) if match else None


def _context_source(row: dict) -> str:
    """Human-readable source identity for the model, including structured scope."""
    source = str(row.get("source") or "source")
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    channel = str(metadata.get("channel_name") or "").strip() if isinstance(metadata, dict) else ""
    return f"{source} · #{channel}" if source == "slack" and channel else source


def live_destination(project_slug: str, destination_slug: str):
    return chat_store.live_destination(project_slug, destination_slug)


def answers_since(project_id: int, usage_detail: str, hours: int = 24) -> int:
    return chat_store.answers_since(project_id, usage_detail, hours)


# ————— session references on the wire —————
#
# A signed-in caller's session is its row id. A public visitor's is
# "<id>.<token>": the published widget stores whatever `session_id` the meta
# event carries and echoes it on the next turn, so the token rides inside that
# one field and an already-deployed widget round-trips it unchanged.

def public_handle(session_id: int, token: str) -> str:
    return f"{session_id}.{token}"


def parse_session(value: int | str | None) -> tuple[int | None, str | None]:
    """(row id, public token) from a wire reference; (None, None) for none.
    Anything that is not an id, optionally followed by a token, is a session
    that does not exist rather than a parse error."""
    if value is None:
        return None, None
    row, _, token = str(value).partition(".")
    if not row.isdigit():
        raise LookupError("Chat session not found.")
    return int(row), token or None


def session_row(value: int | str) -> int:
    return int(parse_session(value)[0])


def resolve_session(project_access: access.AccessContext, session_id: int | str | None,
                    title: str) -> tuple[int, int | str]:
    """The row to write to and the reference to hand back.

    Anonymous access (a published destination) continues a session only with
    its token; a bare id starts a new one, so a widget that still holds an id
    from before tokens keeps working and never lands in someone else's
    conversation. A signed-in caller continues only a session they own.
    """
    project_id = project_access.project_id
    row_id, token = parse_session(session_id)
    if not project_access.user_id:
        if row_id is not None and token is not None:
            if not chat_store.public_session_exists(project_id, row_id, token):
                raise LookupError("Chat session not found.")
        else:
            token = secrets.token_urlsafe(24)
            row_id = chat_store.create_session(project_id, None, title, public_token=token)
        return row_id, public_handle(row_id, token)
    if token is not None:
        raise LookupError("Chat session not found.")
    if row_id is None:
        row_id = chat_store.create_session(project_id, project_access.user_id, title)
    elif not chat_store.session_exists(project_id, project_access.user_id, row_id):
        raise LookupError("Chat session not found.")
    return row_id, row_id


def _pinned_first(documents: list[dict]) -> list[dict]:
    """Move documents pinned on the Workflows page to the front of the context.

    Called inside an access scope, so the pins are the active project's. An
    unavailable read leaves retrieval's own order alone rather than failing the
    turn: a boost that cannot be applied is not an answer that cannot be given.
    """
    try:
        pinned = trajectory_store.pinned_document_ids()
    except Exception:  # noqa: BLE001 -- a missing boost must not break the answer
        return documents
    if not pinned:
        return documents
    return sorted(documents, key=lambda row: row.get("id") not in pinned)


def ports(project_access: access.AccessContext, usage_detail: str,
          enabled_tools: frozenset[str], surface: str = "dock") -> ChatPorts:
    project_id = project_access.project_id
    # Built per request, not at import: a workspace that edits its chat style
    # pack sees the next answer change, without a restart. Mirrors how
    # conversations/agent.py composes its planner prompt per request.
    with access.use_access(project_access):
        system = answer_system(workspace_style_text(), surface)
    selected_state: dict[str, object] = {"workflow": None, "execution_mode": "generation"}

    def prepare(session_id: int | str | None, message: str) -> ChatContext:
        retrieval_question = answer_search_query(message)
        selected_state["workflow"] = workflow = select_workflow(retrieval_question, {"search"})
        cached = workflow_cached_response(workflow)
        retrieval_question = workflow_retrieval_query(retrieval_question, workflow)
        # `session_id` below is the row; `session_ref` is what the client
        # gets back and echoes, which for a public visitor carries the token.
        session_id, session_ref = resolve_session(project_access, session_id, message)
        chat_store.add_message(project_id, session_id, "user", message)

        if cached:
            selected_state["execution_mode"] = "cache"
            return ChatContext(
                session_ref, cached.get("sources") or (), (), cached["answer"], True,
            )

        approved = (chat_store.approved_answer(project_id, message, llm.embed(message))
                    if "answers" in enabled_tools else None)
        if approved:
            # Same field set as a retrieved source: a client renders one card
            # component, and an approved answer is simply a source with no
            # upstream document behind it.
            served = "Approved answer · served verbatim"
            sources = [{"n": 1, "source": "approved", "kind": "answer",
                        "title": approved["question"], "snippet": served, "meta": served,
                        "author": "", "updated": "", "tags": [], "document_id": None,
                        "href": f"/answers?answer={approved['id']}",
                        "source_url": None, "score": 1.0}]
            selected_state["execution_mode"] = "approved_answer"
            return ChatContext(session_ref, sources, (), str(approved["answer"]))

        selected_state["execution_mode"] = "workflow_generation" if workflow else "generation"
        source_urls: dict[int, str] = {}
        with access.use_access(project_access):
            channel_name = requested_slack_channel(retrieval_question)
            documents = (
                slack_channel_search(channel_name, 8) if channel_name and "search" in enabled_tools
                else hybrid_search(retrieval_question, 8) if "search" in enabled_tools
                else []
            )
            # Dedupe before numbering, so the [n] in the context and the n in
            # the payload are the same citation.
            documents = citations.dedupe(documents)
            # Then let the Workflows page's pins speak. Retrieval still decides
            # WHICH documents are relevant to this question; a pin only decides
            # that, among the ones it found, a document somebody vouched for
            # goes in front of the model instead of being cut by the slice
            # below. Stable, so unpinned documents keep their retrieval order.
            documents = _pinned_first(documents)[:4]
            try:
                source_urls = document_store.source_urls(
                    [row["id"] for row in documents if row.get("id") is not None])
            except Exception:
                source_urls = {}
        context = "\n\n".join(
            f"[{i + 1}] {row['title']} ({_context_source(row)})\n{row['body'] or row['snippet']}"
            for i, row in enumerate(documents)
        )
        facts = chat_store.verified_facts(project_id) if "facts" in enabled_tools else []
        context += "\n\nVerified facts:\n" + "\n".join(f"- {row['claim']}" for row in facts)
        sources = citations.source_payload(documents, source_urls=source_urls)
        history = chat_store.messages(project_id, session_id, 10)
        messages = [{"role": row["role"], "content": row["content"]}
                    for row in reversed(history)]
        messages[-1]["content"] = f"Context:\n{context}\n\nQuestion: {retrieval_question}"
        return ChatContext(session_ref, sources, messages)

    def _persist(session_id: int | str, answer: str, sources) -> None:
        # History must match what the reader saw: the cleaned answer, and only
        # the sources it cites. A "could not find" answer stores none, so the
        # transcript never shows four unrelated pages under it. A blank answer
        # stores the warning the reader was shown, and a warning cites nothing.
        text = citations.clean_answer(answer) or MODEL_UNAVAILABLE
        cited = [] if text == MODEL_UNAVAILABLE else citations.cited(text, sources)
        chat_store.add_message(
            project_id, session_row(session_id), "assistant", text, json.dumps(cited),
        )

    return ChatPorts(
        prepare=prepare,
        generate=lambda messages: llm.chat_stream(
            [dict(row) for row in messages],
            system + workflow_guidance(selected_state["workflow"]),
        ),
        persist=_persist,
        record_usage=lambda: log_usage("chat_answer", usage_detail),
        observe=lambda session_id, message, sources, approved: trajectory_store.harvest(
            session_row(session_id), message, [{
                "kind": "tool",
                "name": "read_approved_answer" if approved else "search",
                "args": {"query": message[:200]},
                "summary": ("served approved answer" if approved
                            else f"retrieved {len(sources)} documents"),
                "ok": True,
                "evidence": [{
                    "document_id": source.get("document_id"),
                    "title": source.get("title", ""),
                    "reason": "used as answer context",
                    "rank": source.get("n", 0),
                } for source in sources if source.get("document_id")],
            }], "knowledge-chat-v1",
            int(selected_state["workflow"]["id"]) if selected_state["workflow"] else None,
            execution_mode=str(selected_state["execution_mode"]),
            selected_workflow_score=(float((selected_state["workflow"].get("match") or {})["workflow_score"])
                                     if selected_state["workflow"] and
                                     (selected_state["workflow"].get("match") or {}).get("workflow_score") is not None
                                     else None),
            selected_workflow_exact=bool(selected_state["workflow"] and
                                         (selected_state["workflow"].get("match") or {}).get("exact")),
            observed_cluster_id=(int(selected_state["workflow"]["id"])
                                 if selected_state["workflow"] else None),
        ),
    )

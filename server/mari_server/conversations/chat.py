"""Product adapters for the streaming knowledge-answer use case."""

from __future__ import annotations

import json

from mari_server.identity import context as access
from mari_server.providers import models as llm
from mari_server.persistence.postgres.database import log_usage
from mari_server.persistence.postgres import chat as chat_store
from mari_server.persistence.postgres import documents as document_store
from mari_server.persistence.postgres import trajectories as trajectory_store
from mari_server.search.service import hybrid_search
from mari_server.conversations import citations
from mari_server.conversations.prompts import answer_system, workspace_style_text
from mari_components.destinations.chat import ChatContext, ChatPorts, answer_search_query
from mari_server.conversations.workflows import guidance as workflow_guidance
from mari_server.conversations.workflows import retrieval_query as workflow_retrieval_query
from mari_server.conversations.workflows import select as select_workflow
from mari_server.conversations.workflows import cached_response as workflow_cached_response


def live_destination(project_slug: str, destination_slug: str):
    return chat_store.live_destination(project_slug, destination_slug)


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

    def prepare(session_id: int | None, message: str) -> ChatContext:
        retrieval_question = answer_search_query(message)
        selected_state["workflow"] = workflow = select_workflow(retrieval_question, {"search"})
        cached = workflow_cached_response(workflow)
        retrieval_question = workflow_retrieval_query(retrieval_question, workflow)
        if session_id is None:
            session_id = chat_store.create_session(
                project_id, project_access.user_id or None, message,
            )
        elif not chat_store.session_exists(project_id, project_access.user_id, session_id):
            raise LookupError("Chat session not found.")
        chat_store.add_message(project_id, session_id, "user", message)

        if cached:
            selected_state["execution_mode"] = "cache"
            return ChatContext(
                session_id, cached.get("sources") or (), (), cached["answer"], True,
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
            return ChatContext(session_id, sources, (), str(approved["answer"]))

        selected_state["execution_mode"] = "workflow_generation" if workflow else "generation"
        source_urls: dict[int, str] = {}
        with access.use_access(project_access):
            documents = (hybrid_search(retrieval_question, 8)
                         if "search" in enabled_tools else [])
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
            f"[{i + 1}] {row['title']} ({row['source']})\n{row['body'] or row['snippet']}"
            for i, row in enumerate(documents)
        )
        facts = chat_store.verified_facts(project_id) if "facts" in enabled_tools else []
        context += "\n\nVerified facts:\n" + "\n".join(f"- {row['claim']}" for row in facts)
        sources = citations.source_payload(documents, source_urls=source_urls)
        history = chat_store.messages(project_id, session_id, 10)
        messages = [{"role": row["role"], "content": row["content"]}
                    for row in reversed(history)]
        messages[-1]["content"] = f"Context:\n{context}\n\nQuestion: {retrieval_question}"
        return ChatContext(session_id, sources, messages)

    return ChatPorts(
        prepare=prepare,
        generate=lambda messages: llm.chat_stream(
            [dict(row) for row in messages],
            system + workflow_guidance(selected_state["workflow"]),
        ),
        persist=lambda session_id, answer, sources: chat_store.add_message(
            project_id, session_id, "assistant", answer, json.dumps(list(sources)),
        ),
        record_usage=lambda: log_usage("chat_answer", usage_detail),
        observe=lambda session_id, message, sources, approved: trajectory_store.harvest(
            session_id, message, [{
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

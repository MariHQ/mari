"""Product adapters for the streaming knowledge-answer use case."""

from __future__ import annotations

import json

from mari_server.domain import access
from mari_server.integrations import llm
from mari_server.repositories.database import log_usage
from mari_server.repositories import chat as chat_store
from mari_server.services.search import hybrid_search
from mari_components.destinations.chat import ChatContext, ChatPorts


SYSTEM = (
    "You are Mari, the team's knowledge assistant. Answer from the provided context. "
    "Be concise (2-4 sentences), cite sources as [1], [2]. If the context does not cover it, say so."
)


def live_destination(project_slug: str, destination_slug: str):
    return chat_store.live_destination(project_slug, destination_slug)


def ports(project_access: access.AccessContext, usage_detail: str) -> ChatPorts:
    project_id = project_access.project_id

    def prepare(session_id: int | None, message: str) -> ChatContext:
        if session_id is None:
            session_id = chat_store.create_session(
                project_id, project_access.user_id or None, message,
            )
        elif not chat_store.session_exists(project_id, project_access.user_id, session_id):
            raise LookupError("Chat session not found.")
        chat_store.add_message(project_id, session_id, "user", message)

        approved = chat_store.approved_answer(project_id, message, llm.embed(message))
        if approved:
            sources = [{"n": 1, "source": "approved", "title": approved["question"],
                        "meta": "Approved answer · served verbatim",
                        "href": f"/answers?answer={approved['id']}"}]
            return ChatContext(session_id, sources, (), str(approved["answer"]))

        with access.use_access(project_access):
            documents = hybrid_search(message, 4)
        context = "\n\n".join(
            f"[{i + 1}] {row['title']} ({row['source']})\n{row['body'] or row['snippet']}"
            for i, row in enumerate(documents)
        )
        facts = chat_store.verified_facts(project_id)
        context += "\n\nVerified facts:\n" + "\n".join(f"- {row['claim']}" for row in facts)
        sources = [{"n": i + 1, "source": row["source"], "title": row["title"],
                    "meta": row["snippet"][:110], "document_id": row["id"],
                    "href": f"/knowledge/doc?id={row['id']}"}
                   for i, row in enumerate(documents)]
        history = chat_store.messages(project_id, session_id, 10)
        messages = [{"role": row["role"], "content": row["content"]}
                    for row in reversed(history)]
        messages[-1]["content"] = f"Context:\n{context}\n\nQuestion: {message}"
        return ChatContext(session_id, sources, messages)

    return ChatPorts(
        prepare=prepare,
        generate=lambda messages: llm.chat_stream([dict(row) for row in messages], SYSTEM),
        persist=lambda session_id, answer, sources: chat_store.add_message(
            project_id, session_id, "assistant", answer, json.dumps(list(sources)),
        ),
        record_usage=lambda: log_usage("chat_answer", usage_detail),
    )

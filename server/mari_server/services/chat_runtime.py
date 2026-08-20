"""Product adapters for the streaming knowledge-answer use case."""

from __future__ import annotations

import json

from mari_server.domain import access
from mari_server.integrations import llm
from mari_server.repositories.database import exec_, log_usage, q, q1
from mari_server.services.search import hybrid_search, like_pattern
from mari_server.services.chat import ChatContext, ChatPorts
from mari_server import db as postgres


SYSTEM = (
    "You are Mari, the team's knowledge assistant. Answer from the provided context. "
    "Be concise (2-4 sentences), cite sources as [1], [2]. If the context does not cover it, say so."
)


def live_destination(project_slug: str, destination_slug: str):
    return q1("""SELECT d.id, d.project_id, d.name, d.slug, d.title, d.welcome,
                       p.slug AS project_slug, p.name AS project_name
                  FROM knowledge_chat_destinations d JOIN projects p ON p.id = d.project_id
                 WHERE p.slug = %s AND p.status = 'active'
                   AND d.slug = %s AND d.status = 'live'""",
              (project_slug, destination_slug))


def ports(project_access: access.AccessContext, usage_detail: str) -> ChatPorts:
    project_id = project_access.project_id

    def prepare(session_id: int | None, message: str) -> ChatContext:
        if session_id is None:
            with postgres.connect() as connection:
                row = connection.execute(
                    """INSERT INTO chat_sessions (project_id, owner_user_id, title)
                         VALUES (%s, %s, %s) RETURNING id""",
                    (project_id, project_access.user_id or None, message[:60]),
                ).fetchone()
                session_id = int(row[0])
        elif not q1(
            """SELECT id FROM chat_sessions WHERE id = %s AND project_id = %s
                 AND (owner_user_id = %s OR owner_user_id IS NULL)""",
            (session_id, project_id, project_access.user_id),
        ):
            raise LookupError("Chat session not found.")
        exec_("""INSERT INTO chat_messages (project_id, session_id, role, content)
                  VALUES (%s, %s, 'user', %s)""", (project_id, session_id, message))

        approved = None
        vector = llm.embed(message)
        if vector:
            approved = q1(
                """SELECT id, question, answer, 1 - (embedding <=> %s::vector) AS sim
                     FROM approved_answers WHERE project_id = %s AND status = 'approved'
                       AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT 1""",
                (str(vector), project_id, str(vector)),
            )
            if approved and approved["sim"] < 0.62:
                approved = None
        if approved is None:
            approved = q1(
                """SELECT id, question, answer FROM approved_answers
                     WHERE project_id = %s AND status = 'approved'
                       AND (question ILIKE %s OR position(lower(question) in lower(%s)) > 0)
                     LIMIT 1""", (project_id, like_pattern(message[:60]), message),
            )
        if approved:
            exec_("UPDATE approved_answers SET served = served + 1 WHERE project_id = %s AND id = %s",
                  (project_id, approved["id"]))
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
        facts = q("SELECT claim FROM facts WHERE project_id = %s AND status = 'Verified' LIMIT 8",
                  (project_id,))
        context += "\n\nVerified facts:\n" + "\n".join(f"- {row['claim']}" for row in facts)
        sources = [{"n": i + 1, "source": row["source"], "title": row["title"],
                    "meta": row["snippet"][:110], "document_id": row["id"],
                    "href": f"/knowledge/doc?id={row['id']}"}
                   for i, row in enumerate(documents)]
        history = q("""SELECT role, content FROM chat_messages
                        WHERE project_id = %s AND session_id = %s ORDER BY id DESC LIMIT 10""",
                    (project_id, session_id))
        messages = [{"role": row["role"], "content": row["content"]}
                    for row in reversed(history)]
        messages[-1]["content"] = f"Context:\n{context}\n\nQuestion: {message}"
        return ChatContext(session_id, sources, messages)

    return ChatPorts(
        prepare=prepare,
        generate=lambda messages: llm.chat_stream([dict(row) for row in messages], SYSTEM),
        persist=lambda session_id, answer, sources: exec_(
            """INSERT INTO chat_messages (project_id, session_id, role, content, sources)
                 VALUES (%s, %s, 'assistant', %s, %s)""",
            (project_id, session_id, answer, json.dumps(list(sources))),
        ),
        record_usage=lambda: log_usage("chat_answer", usage_detail),
    )

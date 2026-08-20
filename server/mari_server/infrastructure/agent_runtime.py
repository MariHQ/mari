"""Production adapters for the streaming agent application use case."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from mari_server.domain import access
from mari_server.infrastructure import models as llm
from mari_server.infrastructure import trajectory_repository as trajectory
from mari_server.infrastructure.database import exec_, log_usage, q, q1
from mari_server.infrastructure import postgres
from mari_components.connectors import connector_definitions
from mari_server.infrastructure.search import hybrid_search

from mari_server.application.agent import (
    ANSWER_INSTRUCTIONS,
    AgentPorts,
    ToolBinding,
    planner_instructions,
)
from mari_server.infrastructure.agent_tools import ToolDependencies, build_tool_bindings
from mari_server.infrastructure import review_repository


@dataclass(frozen=True, slots=True)
class ProductionAgentRuntime:
    """All product-specific persistence and model dependencies for one request."""

    project_access: access.AccessContext

    @property
    def project_id(self) -> int:
        return self.project_access.project_id

    def create_session(self, title: str) -> int:
        with postgres.connect() as connection:
            row = connection.execute(
                """INSERT INTO chat_sessions (project_id, owner_user_id, title)
                     VALUES (%s, %s, %s) RETURNING id""",
                (self.project_id, self.project_access.user_id or None, title[:60]),
            ).fetchone()
        return int(row[0])

    def require_session(self, session_id: int) -> None:
        row = q1(
            """SELECT id FROM chat_sessions WHERE id = %s AND project_id = %s
                 AND (owner_user_id = %s OR owner_user_id IS NULL)""",
            (session_id, self.project_id, self.project_access.user_id),
        )
        if not row:
            raise LookupError("Chat session not found.")

    def append_user_message(self, session_id: int, message: str) -> None:
        exec_(
            """INSERT INTO chat_messages (project_id, session_id, role, content)
                 VALUES (%s, %s, 'user', %s)""",
            (self.project_id, session_id, message),
        )

    def bindings(self) -> dict[str, ToolBinding]:
        dependencies = ToolDependencies(
            project_id=self.project_id,
            query=lambda sql, params: q(sql, params),
            query_one=lambda sql, params: q1(sql, params),
            search=lambda text, limit: hybrid_search(text, limit),
            record_search=lambda text: log_usage("search", text),
            review_items=review_repository.project_items,
            connector_definitions=connector_definitions,
        )
        return build_tool_bindings(dependencies)

    def ports(self, bindings: dict[str, ToolBinding]) -> AgentPorts:
        system = planner_instructions(bindings)

        def history(session_id: int):
            rows = q(
                """SELECT role, content FROM chat_messages
                     WHERE project_id = %s AND session_id = %s ORDER BY id DESC LIMIT 12""",
                (self.project_id, session_id),
            )
            return [
                {"role": str(row["role"]), "content": str(row["content"])[:2000]}
                for row in reversed(rows)
            ]

        def save_answer(session_id: int, answer: str, trace) -> None:
            exec_(
                """INSERT INTO chat_messages (project_id, session_id, role, content, sources)
                     VALUES (%s, %s, 'assistant', %s, %s)""",
                (self.project_id, session_id, answer, json.dumps(list(trace))),
            )

        return AgentPorts(
            history=history,
            plan=lambda prompt, _version: llm.generate_json(
                prompt, system=system, timeout=90.0,
            ),
            answer=lambda transcript: llm.chat_stream(
                [dict(row) for row in transcript], system=ANSWER_INSTRUCTIONS,
            ),
            save_answer=save_answer,
            observe_trajectory=lambda session_id, message, trace, version: trajectory.harvest(
                session_id, message, list(trace), version,
            ),
            record_usage=log_usage,
        )


def production_runtime(project_access: access.AccessContext) -> ProductionAgentRuntime:
    return ProductionAgentRuntime(project_access)

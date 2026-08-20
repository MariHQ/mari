"""Postgres and audit adapters for knowledge-chat destinations."""

from __future__ import annotations

from db import audit, transaction
from mari_server.application.knowledge_chat import KnowledgeChatPorts


def _create(project_id: int, name: str, slug: str, title: str, welcome: str) -> int:
    def create(conn):
        if conn.execute(
            """SELECT 1 FROM knowledge_chat_destinations
                 WHERE project_id = %s AND (name = %s OR slug = %s)""",
            (project_id, name, slug),
        ).fetchone():
            raise ValueError("A knowledge chat with that name or URL slug already exists.")
        row = conn.execute(
            """INSERT INTO knowledge_chat_destinations (project_id, name, slug, title, welcome)
                 VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (project_id, name, slug, title, welcome),
        ).fetchone()
        return int(row["id"])
    return transaction(create)


def _update(project_id: int, destination_id: int, name: str, title: str, welcome: str) -> bool:
    return bool(transaction(lambda conn: conn.execute(
        """UPDATE knowledge_chat_destinations SET name = %s, title = %s, welcome = %s,
             updated_at = now() WHERE project_id = %s AND id = %s RETURNING id""",
        (name, title, welcome, project_id, destination_id),
    ).fetchone()))


def _deploy(project_id: int, destination_id: int):
    def deploy(conn):
        row = conn.execute(
            """SELECT d.name, d.slug, p.slug AS project_slug
                 FROM knowledge_chat_destinations d JOIN projects p ON p.id = d.project_id
                WHERE d.project_id = %s AND d.id = %s FOR UPDATE OF d""",
            (project_id, destination_id),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """UPDATE knowledge_chat_destinations SET status = 'live', updated_at = now()
                 WHERE project_id = %s AND id = %s""", (project_id, destination_id),
        )
        return str(row["name"]), f"/knowledge-chat/{row['project_slug']}/{row['slug']}"
    return transaction(deploy)


def ports() -> KnowledgeChatPorts:
    return KnowledgeChatPorts(
        create=_create, update=_update, deploy=_deploy,
        audit=lambda verb, target: audit(verb, target),
    )

"""Postgres and secret adapters for MCP destination lifecycle."""

from __future__ import annotations

import json
import secrets

from mari_server.repositories.database import audit, exec_, jload, q1
from mari_server.services.mcp import McpPorts
from mari_server.domain.mcp import McpServerSpec


def list_servers(project_id: int) -> list[dict]:
    from mari_server.repositories.database import q
    return q("SELECT * FROM mcp_servers WHERE project_id = %s ORDER BY id", (project_id,))


def facts(status: str | None = None) -> list[dict]:
    from mari_server.domain import access
    project_id = access.require_current_access().project_id
    sql = "SELECT id, claim, source, status FROM facts WHERE project_id = %s"
    args: tuple = (project_id,)
    if status:
        sql += " AND status = %s"
        args += (status,)
    return q(sql + " ORDER BY id LIMIT 200", args)


def glossary() -> list[dict]:
    from mari_server.domain import access
    return q("""SELECT id, term, definition FROM glossary
      WHERE project_id = %s AND NOT candidate ORDER BY term LIMIT 200""",
      (access.require_current_access().project_id,))


def answers() -> list[dict]:
    from mari_server.domain import access
    return q("""SELECT id, question, answer FROM approved_answers
      WHERE project_id = %s AND status = 'approved' ORDER BY id LIMIT 200""",
      (access.require_current_access().project_id,))


def lineage(document_id: int) -> list[dict]:
    from mari_server.domain import access
    project_id = access.require_current_access().project_id
    return q("""SELECT e.id, e.rel, f.id AS from_id, f.title AS from_title,
      t.id AS to_id, t.title AS to_title FROM edges e
      JOIN documents f ON f.id = e.from_doc JOIN documents t ON t.id = e.to_doc
      WHERE e.project_id = %s AND f.project_id = e.project_id AND t.project_id = e.project_id
      AND (e.from_doc = %s OR e.to_doc = %s) ORDER BY e.id LIMIT 200""",
      (project_id, document_id, document_id))


def authenticate(token_hash: str, legacy_token: str) -> dict | None:
    return q1("""SELECT m.id, m.name, m.config, m.project_id,
      p.slug AS project_slug, p.name AS project_name FROM mcp_servers m
      JOIN projects p ON p.id = m.project_id
      WHERE (m.token_hash = %s OR (m.token <> '' AND m.token = %s))
      AND m.status = 'connected' AND p.status = 'active'""", (token_hash, legacy_token))


def _name_exists(project_id: int, name: str) -> bool:
    return bool(q1(
        "SELECT id FROM mcp_servers WHERE project_id = %s AND name = %s", (project_id, name),
    ))


def _insert(project_id: int, spec: McpServerSpec, url: str, token_hash: str, tools: int) -> None:
    exec_(
        """INSERT INTO mcp_servers
             (project_id, name, url, scope, status, tools, config, token, token_hash)
             VALUES (%s, %s, %s, %s, 'connected', %s, %s, '', %s)""",
        (project_id, spec.name, url, spec.scope, tools,
         json.dumps({"capabilities": list(spec.capabilities)}), token_hash),
    )


def _update(project_id: int, server_id: int, scope, capabilities) -> bool:
    row = q1(
        "SELECT id FROM mcp_servers WHERE project_id = %s AND id = %s", (project_id, server_id),
    )
    if not row:
        return False
    if scope is not None:
        exec_("UPDATE mcp_servers SET scope = %s WHERE project_id = %s AND id = %s",
              (scope, project_id, server_id))
    if capabilities is not None:
        exec_(
            """UPDATE mcp_servers SET config = jsonb_set(config, '{capabilities}', %s), tools = %s
                 WHERE project_id = %s AND id = %s""",
            (json.dumps(list(capabilities)), len(capabilities), project_id, server_id),
        )
    return True


def _delete(project_id: int, server_id: int) -> str | None:
    row = q1(
        "DELETE FROM mcp_servers WHERE project_id = %s AND id = %s RETURNING name",
        (project_id, server_id),
    )
    return str(row["name"]) if row else None


def _inspect(project_id: int, server_id: int):
    row = q1("SELECT config FROM mcp_servers WHERE project_id = %s AND id = %s",
             (project_id, server_id))
    return {"capabilities": (jload(row["config"]) or {}).get("capabilities", ["search"])} if row else None


def _counts(project_id: int, capabilities) -> dict[str, int]:
    statements = {
        "search": "SELECT count(*) AS n FROM documents WHERE project_id = %s",
        "facts": "SELECT count(*) AS n FROM facts WHERE project_id = %s",
        "glossary": "SELECT count(*) AS n FROM glossary WHERE project_id = %s AND NOT candidate",
        "answers": "SELECT count(*) AS n FROM approved_answers WHERE project_id = %s AND status = 'approved'",
        "lineage": "SELECT count(*) AS n FROM edges WHERE project_id = %s",
    }
    result = {"chat": 1} if "chat" in capabilities else {}
    for capability in capabilities:
        if capability in statements:
            result[capability] = int(q1(statements[capability], (project_id,))["n"])
    return result


def ports() -> McpPorts:
    return McpPorts(
        name_exists=_name_exists, insert=_insert, update=_update, delete=_delete,
        inspect=_inspect, capability_counts=_counts,
        mark_connected=lambda project_id, server_id: exec_(
            "UPDATE mcp_servers SET status = 'connected' WHERE project_id = %s AND id = %s",
            (project_id, server_id),
        ),
        audit=lambda verb, target, detail: audit(verb, target, detail=list(detail)),
        issue_token=lambda: "mari_mcp_" + secrets.token_hex(12),
    )

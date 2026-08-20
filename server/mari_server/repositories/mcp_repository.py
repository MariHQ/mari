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

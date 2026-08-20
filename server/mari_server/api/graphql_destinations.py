"""GraphQL transport for interactive chat and MCP destinations."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.infrastructure import config
from mari_server.api.graphql_admin import _require_admin
from mari_server.application import knowledge_chat, mcp
from mari_server.infrastructure import knowledge_chat_repository, mcp_repository


def _project(info: strawberry.Info):
    project = info.context.get("access")
    if project is None:
        raise PermissionError("Choose a project.")
    return project


@strawberry.type
class DestinationMutations:
    @strawberry.mutation
    def create_knowledge_chat_destination(self, info: strawberry.Info, name: str, slug: str,
                                          title: str, welcome: str = "") -> int:
        _require_admin(info)
        return knowledge_chat.create(_project(info).project_id, name, slug, title, welcome,
                                     ports=knowledge_chat_repository.ports())

    @strawberry.mutation
    def update_knowledge_chat_destination(self, info: strawberry.Info, id: int, name: str,
                                          title: str, welcome: str) -> bool:
        _require_admin(info)
        return knowledge_chat.update(_project(info).project_id, id, name, title, welcome,
                                     ports=knowledge_chat_repository.ports())

    @strawberry.mutation
    def deploy_knowledge_chat_destination(self, info: strawberry.Info, id: int) -> str:
        _require_admin(info)
        return knowledge_chat.deploy(_project(info).project_id, id,
                                     ports=knowledge_chat_repository.ports())

    @strawberry.mutation
    def create_mcp_server(self, info: strawberry.Info, name: str, scope: str,
                          capabilities: JSON) -> str:
        _require_admin(info)
        project = _project(info)
        if not project.allows("destination.manage"):
            raise PermissionError("This action requires destination.manage.")
        base = str(config.get("auth", "oauth_redirect_base") or "http://localhost:8000").rstrip("/")
        return mcp.create_server(project.project_id, name, scope, capabilities,
                                 base_url=base, ports=mcp_repository.ports())

    @strawberry.mutation
    def update_mcp_server(self, info: strawberry.Info, id: int, scope: str | None = None,
                          capabilities: JSON = None) -> bool:
        _require_admin(info)
        return mcp.update_server(_project(info).project_id, id, scope=scope,
                                 capabilities=capabilities, ports=mcp_repository.ports())

    @strawberry.mutation
    def delete_mcp_server(self, info: strawberry.Info, id: int) -> bool:
        _require_admin(info)
        return mcp.delete_server(_project(info).project_id, id, ports=mcp_repository.ports())

    @strawberry.mutation
    def test_mcp_server(self, info: strawberry.Info, id: int) -> JSON:
        project = info.context.get("access")
        if project is None:
            return {"ok": False, "error": "project required"}
        return mcp.test_server(project.project_id, id, ports=mcp_repository.ports())

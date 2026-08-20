"""GraphQL transport for workflow lifecycle commands."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.api import access
from mari_server.application import workflows
from mari_server.infrastructure import workflow_repository


@strawberry.type
class WorkflowMutations:
    @strawberry.mutation
    def run_workflow(self, workflow_id: int, dry_run: bool = False) -> int:
        project_id = access.require_current_access().project_id
        return workflows.run(project_id, workflow_id, dry_run=dry_run,
                             ports=workflow_repository.ports())

    @strawberry.mutation
    def approve_run(self, info: strawberry.Info, run_id: int) -> bool:
        project_id = access.require_current_access().project_id
        user = info.context.get("user") or {}
        return workflows.approve(project_id, run_id, actor_name=str(user.get("name") or "Mari"),
                                 ports=workflow_repository.ports())

    @strawberry.mutation
    def save_workflow(self, name: str, description: str, steps: JSON,
                      id: int | None = None, color: str = "#5c7a4c", pinned: bool = True) -> int:
        project_id = access.require_current_access().project_id
        return workflows.save(project_id, name, description, steps, workflow_id=id,
                              color=color, pinned=pinned, ports=workflow_repository.ports())

    @strawberry.mutation
    def delete_workflow(self, id: int) -> bool:
        return workflows.delete(access.require_current_access().project_id, id,
                                ports=workflow_repository.ports())

    @strawberry.mutation
    def set_workflow_status(self, id: int, status: str) -> bool:
        return workflows.set_status(access.require_current_access().project_id, id, status,
                                    ports=workflow_repository.ports())

    @strawberry.mutation
    def set_workflow_pinned(self, id: int, pinned: bool) -> bool:
        project_id = access.require_current_access().project_id
        return workflow_repository.ports().set_pinned(project_id, id, pinned)

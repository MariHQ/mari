"""GraphQL transport for workflow lifecycle commands."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.identity import access
from mari_components import workflows
from mari_server.persistence.postgres import workflows as workflow_repository
from mari_server.automations import runtime

DEPRECATED_EDITOR = (
    "The Flows pipeline editor was removed; nothing in the product "
    "authors workflows any more. Scheduled syncs and promoted workflows "
    "still run. Kept for one release for existing API clients."
)


@strawberry.type
class WorkflowMutations:
    @staticmethod
    def _ports():
        return workflow_repository.ports(runtime.start_run)

    @strawberry.mutation
    def run_workflow(self, workflow_id: int, dry_run: bool = False) -> int:
        project_id = access.require_current_access().project_id
        return workflows.run(project_id, workflow_id, dry_run=dry_run,
                             ports=WorkflowMutations._ports())

    @strawberry.mutation
    def approve_run(self, info: strawberry.Info, run_id: int) -> bool:
        project_id = access.require_current_access().project_id
        user = info.context.get("user") or {}
        return workflows.approve(project_id, run_id, actor_name=str(user.get("name") or "Mari"),
                                 ports=WorkflowMutations._ports())

    @strawberry.mutation
    def dismiss_workflow_run(self, run_id: int) -> bool:
        """Hide a completed or waiting run from this user's recovered workspace."""
        return workflow_repository.dismiss_run(run_id)

    @strawberry.mutation(deprecation_reason=DEPRECATED_EDITOR)
    def save_workflow(self, name: str, description: str, steps: JSON,
                      id: int | None = None, color: str = "#5c7a4c", pinned: bool = True) -> int:
        project_id = access.require_current_access().project_id
        return workflows.save(project_id, name, description, steps, workflow_id=id,
                              color=color, pinned=pinned, ports=WorkflowMutations._ports())

    @strawberry.mutation(deprecation_reason=DEPRECATED_EDITOR)
    def delete_workflow(self, id: int) -> bool:
        return workflows.delete(access.require_current_access().project_id, id,
                                ports=WorkflowMutations._ports())

    @strawberry.mutation
    def set_workflow_status(self, id: int, status: str) -> bool:
        return workflows.set_status(access.require_current_access().project_id, id, status,
                                    ports=WorkflowMutations._ports())

    @strawberry.mutation(deprecation_reason=DEPRECATED_EDITOR)
    def set_workflow_pinned(self, id: int, pinned: bool) -> bool:
        project_id = access.require_current_access().project_id
        return WorkflowMutations._ports().set_pinned(project_id, id, pinned)

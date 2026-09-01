"""GraphQL transport for workflow lifecycle commands."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.identity import access
from mari_components import workflows
from mari_server.persistence.postgres import workflows as workflow_repository
from mari_server.persistence.postgres.database import jload
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

    @strawberry.mutation
    def create_scheduled_task(self, kind: str, every_minutes: int = 0) -> int:
        """Recreate one of the console's recurring jobs, with a cadence.

        Removal deletes the workflow outright, and until now only a server
        restart (which re-seeds) could bring a job back. Idempotent: the
        existing flow of that kind is reused and only its cadence changes.
        Decision scan is not offered — it is deliberately manual-only."""
        cadences = {0, 10, 15, 60, 360, 1440, 10080}
        if every_minutes not in cadences:
            raise ValueError("Unknown cadence.")
        if kind == "facts":
            workflow_id = int(runtime.ensure_fact_scan_flow())
        elif kind == "digest":
            created = runtime.ensure_digest_flow()
            existing = workflow_repository.find_by_step("refresh_digest")
            workflow_id = int(created or (existing or {}).get("id") or 0)
        else:
            raise ValueError("Unknown scheduled task kind.")
        if not workflow_id:
            raise ValueError("The task could not be created.")
        if every_minutes:
            workflow_repository.set_trigger(
                workflow_id, {"on": "schedule", "every_minutes": every_minutes})
        return workflow_id

    @strawberry.mutation
    def remove_scheduled_task(self, task_id: int) -> bool:
        """Remove a recurring/background task and its run history.

        This command is deliberately narrower than the retired workflow
        editor delete: an observed/assistant trajectory cannot be deleted from
        the task manager, and an executing task must finish first.
        """
        project_id = access.require_current_access().project_id
        row = next((item for item in workflow_repository.list_workflows()
                    if int(item["id"]) == task_id), None)
        if not row:
            return False
        nodes = jload(row.get("nodes")) or []
        trigger = jload(row.get("trigger")) or {}
        task_steps = {"sync_source", "scan_facts", "refresh_digest"}
        is_task = trigger.get("on") == "schedule" or any(
            isinstance(node, dict) and node.get("kind") in task_steps for node in nodes)
        if not is_task or row.get("status") == "archived":
            raise ValueError("This item is not a scheduled task.")
        if row.get("last_run_status") == "running":
            raise ValueError("This task is still running. Wait for it to finish, then remove it.")
        return workflows.delete(project_id, task_id, ports=WorkflowMutations._ports())

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

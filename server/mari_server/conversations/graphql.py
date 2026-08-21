"""Human governance mutations for observed agent trajectories."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.persistence.postgres.database import audit
from mari_server.persistence.postgres import trajectories
from mari_server.conversations import workflows as workflow_service


def _access(info: strawberry.Info):
    project = info.context.get("access")
    if project is None or not project.allows("automation.manage"):
        raise PermissionError("This action requires automation.manage.")
    return project


@strawberry.type
class TrajectoryMutations:
    @strawberry.mutation
    def tune_trajectory_step(self, info: strawberry.Info, trajectory_id: int,
                             ordinal: int, disposition: str,
                             edited_args: JSON | None = None) -> bool:
        _access(info)
        changed = trajectories.tune_step(
            trajectory_id, ordinal, disposition.strip().lower(), edited_args,
        )
        if changed:
            audit("tuned trajectory tool", f"trajectory:{trajectory_id}:step:{ordinal}",
                  detail=[("disposition", disposition)])
        return changed

    @strawberry.mutation
    def tune_trajectory_evidence(self, info: strawberry.Info, trajectory_id: int,
                                 document_id: int, relevance: str, note: str = "") -> bool:
        _access(info)
        changed = trajectories.tune_evidence(
            trajectory_id, document_id, relevance.strip().lower(), note,
        )
        if changed:
            audit("tuned trajectory evidence", f"trajectory:{trajectory_id}:document:{document_id}",
                  detail=[("relevance", relevance)])
        return changed

    @strawberry.mutation
    def promote_trajectory_to_workflow(self, info: strawberry.Info,
                                       trajectory_id: int, name: str) -> int:
        _access(info)
        workflow_id = trajectories.promote_to_workflow(trajectory_id, name)
        audit("promoted trajectory", f"trajectory:{trajectory_id}",
              detail=[("workflow", workflow_id)])
        return workflow_id

    @strawberry.mutation
    def set_assistant_workflow_enabled(self, info: strawberry.Info,
                                       workflow_id: int, enabled: bool) -> bool:
        _access(info)
        changed = trajectories.set_workflow_enabled(workflow_id, enabled)
        if changed:
            audit("enabled assistant workflow" if enabled else "paused assistant workflow",
                  f"assistant-workflow:{workflow_id}")
        return changed

    @strawberry.mutation
    def set_assistant_workflow_cache(self, info: strawberry.Info,
                                     workflow_id: int, enabled: bool) -> bool:
        _access(info)
        changed = trajectories.configure_workflow_cache(workflow_id, enabled)
        if changed:
            audit("enabled assistant workflow cache" if enabled
                  else "disabled assistant workflow cache",
                  f"assistant-workflow:{workflow_id}")
        return changed

    @strawberry.mutation
    def reconcile_stale_assistant_workflows(self, info: strawberry.Info,
                                            limit: int = 50) -> int:
        _access(info)
        reconciled = workflow_service.reconcile_stale(limit)
        audit("reconciled stale assistant workflows", "assistant-workflows",
              detail=[("count", reconciled)])
        return reconciled

"""Human governance mutations for observed agent trajectories."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.identity.actor import actor_name
from mari_server.persistence.postgres.database import audit
from mari_server.persistence.postgres import trajectories
from mari_server.product.types import PromotedWorkflow
from mari_server.conversations import workflows as workflow_service


def _access(info: strawberry.Info):
    project = info.context.get("access")
    if project is None or not project.allows("automation.manage"):
        raise PermissionError("This action requires automation.manage.")
    return project


@strawberry.type
class TrajectoryMutations:
    @strawberry.mutation
    def harvest_workflow_candidates(self, info: strawberry.Info,
                                    limit: int = 100) -> JSON:
        _access(info)
        return workflow_service.harvest_candidates(limit)

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
                                       trajectory_id: int, name: str) -> PromotedWorkflow:
        """Codify the tuned trace as a paused workflow.

        Answers with the workflow rather than its id so the card can show what
        was made without a second read: a promotion whose result is invisible
        looks exactly like a button that did nothing."""
        _access(info)
        row = trajectories.trajectory_for_split(trajectory_id)
        matched = None
        if row and not row.get("workflow_id"):
            selected = workflow_service.select(str(row.get("prompt") or ""), None)
            matched = int(selected["id"]) if selected else None
        workflow_id = trajectories.promote_to_workflow(
            trajectory_id, name, matched_workflow_id=matched,
        )
        clustered = workflow_service.cluster_unassigned()
        audit("promoted trajectory", f"trajectory:{trajectory_id}",
              detail=[("workflow", workflow_id), ("clustered observations", clustered)])
        made = trajectories.promoted_workflow_summary(workflow_id) or {}
        return PromotedWorkflow(id=int(workflow_id), name=str(made.get("name") or name),
                                status=str(made.get("status") or "active"),
                                node_count=int(made.get("node_count") or 0))

    @strawberry.mutation
    def suggest_workflow_split_name(self, info: strawberry.Info,
                                    trajectory_id: int) -> str:
        _access(info)
        return workflow_service.suggest_split_name(trajectory_id)

    @strawberry.mutation
    def split_assistant_workflow(self, info: strawberry.Info,
                                 trajectory_id: int, name: str) -> int:
        _access(info)
        workflow_id = trajectories.split_workflow(trajectory_id, name)
        audit("split assistant workflow", f"trajectory:{trajectory_id}",
              detail=[("workflow", workflow_id), ("name", name)])
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
    def delete_assistant_workflow(self, info: strawberry.Info,
                                  workflow_id: int) -> bool:
        _access(info)
        changed = trajectories.delete_workflow(workflow_id)
        if changed:
            audit("deleted assistant workflow", f"assistant-workflow:{workflow_id}")
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
    @strawberry.mutation
    def promote_trajectory_to_answer(self, info: strawberry.Info,
                                     trajectory_id: int) -> int:
        """Draft an approved answer from what the agent answered in this run.

        It lands as a draft and is embedded only when somebody approves it, so
        promoting never changes what a bot is serving right now."""
        _access(info)
        answer_id = trajectories.promote_to_answer(trajectory_id, actor_name())
        audit("promoted trajectory to answer", f"trajectory:{trajectory_id}",
              detail=[("answer", answer_id)])
        return answer_id

    @strawberry.mutation
    def reject_trajectory(self, info: strawberry.Info, trajectory_id: int,
                          rejected: bool = True) -> bool:
        """Turn a run down without losing it. The evidence stays readable.

        `rejected: false` restores it. Rejection is a judgement, not a delete,
        so it has to be reversible by the same control that made it."""
        _access(info)
        disposition = "rejected" if rejected else "observed"
        changed = trajectories.set_disposition(trajectory_id, disposition)
        if changed:
            audit("rejected trajectory" if rejected else "restored trajectory",
                  f"trajectory:{trajectory_id}")
        return changed

    @strawberry.mutation
    def delete_trajectory(self, info: strawberry.Info, trajectory_id: int) -> bool:
        """Remove a run and everything harvested with it. Not reversible."""
        _access(info)
        removed = trajectories.delete_trajectory(trajectory_id)
        if removed:
            audit("deleted trajectory", f"trajectory:{trajectory_id}")
        return removed

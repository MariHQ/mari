"""Human governance mutations for observed agent trajectories."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from mari_server.identity.actor import actor_name
from mari_server.persistence.postgres.database import audit
from mari_server.persistence.postgres import trajectories
from mari_server.product.types import PromotedWorkflow


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
                                       trajectory_id: int, name: str) -> PromotedWorkflow:
        """Codify the tuned trace as a paused workflow.

        Answers with the workflow rather than its id so the card can show what
        was made without a second read: a promotion whose result is invisible
        looks exactly like a button that did nothing."""
        _access(info)
        made = trajectories.promote_to_workflow(trajectory_id, name)
        audit("promoted trajectory", f"trajectory:{trajectory_id}",
              detail=[("workflow", made["id"])])
        return PromotedWorkflow(id=int(made["id"]), name=str(made["name"]),
                                status=str(made["status"]),
                                node_count=int(made["node_count"]))

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

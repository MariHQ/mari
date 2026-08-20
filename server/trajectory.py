"""Privacy-bounded LLM trajectory harvesting and hierarchical workflow mining.

The progressive abstraction mirrors rt-intent's WorkflowView work:
chronological tool telemetry -> grounded detailed workflow -> succinct inferred
activity -> discovered/assigned taxonomy. A deterministic coarse-to-fine phase
tree remains available when Ollama is unavailable and gives every LLM summary
auditable step boundaries.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import llm
import access
from db import exec_, q, q1
from mari_components.trajectories import (
    TrajectoryStep as ComponentTrajectoryStep,
    analyze_trajectory as component_analyze_trajectory,
    normalize_steps as component_normalize_steps,
    rework_count as component_rework_count,
    segment_phases as component_segment_phases,
)

_WORKER_COUNT = max(1, int(os.environ.get("MARI_TRAJECTORY_WORKERS", "2")))
_PENDING_LIMIT = max(_WORKER_COUNT, int(os.environ.get("MARI_TRAJECTORY_PENDING", "32")))
_WORKERS = ThreadPoolExecutor(max_workers=_WORKER_COUNT, thread_name_prefix="trajectory-harvest")
_PENDING = threading.BoundedSemaphore(_PENDING_LIMIT)
_RECONCILE_LOCK = threading.Lock()
_LAST_RECONCILE: dict[int, float] = {}

FAMILY = {
    "search": "discover", "read_document": "inspect", "list_sources": "inspect",
    "list_flows": "inspect", "list_tasks": "inspect", "list_answers": "inspect",
    "tag_document": "change", "untag_document": "change",
    "create_task": "change", "approve_answer": "approve", "sync_source": "execute",
    "run_flow": "execute", "navigate": "navigate",
}


def normalize_steps(trace: list[dict]) -> list[dict]:
    return [
        {
            "ordinal": step.ordinal,
            "tool": step.tool,
            "action_family": step.action_family,
            "args": dict(step.arguments),
            "summary": step.summary,
            "ok": step.ok,
        }
        for step in component_normalize_steps(trace, family_map=FAMILY)
    ]


def _component_steps(steps: list[dict]) -> tuple[ComponentTrajectoryStep, ...]:
    return tuple(
        ComponentTrajectoryStep(
            int(step["ordinal"]), str(step["tool"]), str(step["action_family"]),
            dict(step.get("args") or {}), str(step.get("summary") or ""), bool(step.get("ok")),
        )
        for step in steps
    )


def segment_phases(steps: list[dict]) -> list[dict]:
    """Coarse-to-fine phases from action-family changes and failure recovery.

    Short product traces do not support statistically honest KMeans. This
    online equivalent uses the hierarchy's observable signals: family shifts
    define phase boundaries and failure -> later success marks a recovery
    sub-state. Adjacent one-step phases of the same family are always merged.
    """
    return [
        {
            "id": phase.identifier, "name": phase.name, "family": phase.family,
            "start": phase.start, "end": phase.end, "steps": phase.steps,
            "substate": phase.substate, "failures": phase.failures,
        }
        for phase in component_segment_phases(_component_steps(steps))
    ]


def rework_count(steps: list[dict]) -> int:
    return component_rework_count(_component_steps(steps))


def _fallback(trajectory_id: int, steps: list[dict], project_id: int,
              macro_intent: str = "Unavailable") -> None:
    phases = segment_phases(steps)
    grounded = "; ".join(f"{p['name']} ({p['steps']} steps)" for p in phases) or "No tool actions"
    exec_("""UPDATE trajectories SET status = 'fallback', layer1 = %s, layer2 = %s,
                category = %s, macro_intent = %s, phases = %s, completed_at = now()
              WHERE id = %s AND project_id = %s""",
          (grounded, grounded, phases[0]["name"] if phases else "Unclassified",
           macro_intent, json.dumps(phases), trajectory_id, project_id))


def _submit(project_access: access.AccessContext, trajectory_id: int,
            prompt: str, steps: list[dict]) -> bool:
    """Submit without ever growing ThreadPoolExecutor's private queue."""
    if not _PENDING.acquire(blocking=False):
        return False

    def run() -> None:
        with access.use_access(project_access):
            analyze(trajectory_id, prompt, steps)

    try:
        future = _WORKERS.submit(run)
    except Exception:
        _PENDING.release()
        raise
    future.add_done_callback(lambda _future: _PENDING.release())
    return True


def analyze(trajectory_id: int, prompt: str, steps: list[dict]) -> None:
    project_id = access.require_current_access().project_id
    try:
        existing = [r["category"] for r in q(
            """SELECT category FROM trajectories WHERE project_id = %s
               AND category <> 'Unclassified' GROUP BY category ORDER BY count(*) DESC LIMIT 20""",
            (project_id,))]
        analysis = component_analyze_trajectory(
            prompt,
            [{"name": step["tool"], "args": step.get("args") or {},
              "summary": step.get("summary") or "", "ok": bool(step.get("ok"))}
             for step in steps],
            generate_json=lambda request, _version: llm.generate_json(request),
            taxonomy=existing,
            family_map=FAMILY,
        )
        phases = [{
            "id": phase.identifier, "name": phase.name, "family": phase.family,
            "start": phase.start, "end": phase.end, "steps": phase.steps,
            "substate": phase.substate, "failures": phase.failures,
        } for phase in analysis.phases]
        exec_("""UPDATE trajectories SET status = 'ready', layer1 = %s, layer2 = %s,
                    category = %s, macro_intent = %s, phases = %s, completed_at = now()
                  WHERE id = %s AND project_id = %s""",
              (analysis.grounded_workflow, analysis.activity, analysis.category,
               analysis.macro_intent, json.dumps(phases), trajectory_id, project_id))
    except Exception as error:  # noqa: BLE001 -- keep grounded fallback available
        _fallback(trajectory_id, steps, project_id)


def reconcile_stale_processing(stale_minutes: int = 15, limit: int = 32) -> int:
    """Atomically reclaim stale analyses for the active project.

    A process death leaves ``processing`` behind.  Rows are first changed to
    ``reconciling`` with SKIP LOCKED so multiple API workers cannot enqueue the
    same recovery.  Saturated recovery work is finalized deterministically
    instead of remaining stuck forever.
    """
    project_access = access.require_current_access()
    stale_minutes = max(1, min(int(stale_minutes), 24 * 60))
    limit = max(1, min(int(limit), _PENDING_LIMIT))
    rows = q("""WITH stale AS (
                  SELECT id FROM trajectories
                   WHERE project_id = %s AND status IN ('processing', 'reconciling')
                     AND completed_at IS NULL
                     AND started_at < now() - (%s * interval '1 minute')
                   ORDER BY started_at, id FOR UPDATE SKIP LOCKED LIMIT %s
                )
                UPDATE trajectories t SET status = 'reconciling', started_at = now()
                  FROM stale WHERE t.id = stale.id
                RETURNING t.id, t.prompt""",
             (project_access.project_id, stale_minutes, limit))
    for row in rows:
        step_rows = q("""SELECT ordinal, tool, action_family, args, summary, ok
                           FROM trajectory_steps WHERE project_id = %s AND trajectory_id = %s
                           ORDER BY ordinal""", (project_access.project_id, row["id"]))
        steps = [{**step, "args": step["args"] if isinstance(step.get("args"), dict)
                  else json.loads(step.get("args") or "{}")} for step in step_rows]
        if not _submit(project_access, int(row["id"]), str(row.get("prompt") or ""), steps):
            _fallback(int(row["id"]), steps, project_access.project_id, "Recovered without LLM")
    return len(rows)


def _maybe_reconcile(project_access: access.AccessContext) -> None:
    now = time.monotonic()
    with _RECONCILE_LOCK:
        if now - _LAST_RECONCILE.get(project_access.project_id, 0.0) < 300:
            return
        _LAST_RECONCILE[project_access.project_id] = now
    try:
        reconcile_stale_processing()
    except Exception:  # noqa: BLE001 -- recovery cannot break a new user turn
        with _RECONCILE_LOCK:
            _LAST_RECONCILE.pop(project_access.project_id, None)


def harvest(session_id: int, prompt: str, trace: list[dict], model: str) -> int:
    project_access = access.require_current_access()
    _maybe_reconcile(project_access)
    steps = normalize_steps(trace)
    row = q1("""INSERT INTO trajectories
                  (project_id, session_id, prompt, status, model, step_count, failure_count, rework_count, phases)
                SELECT %s, s.id, %s, 'processing', %s, %s, %s, %s, %s
                  FROM chat_sessions s WHERE s.id = %s AND s.project_id = %s RETURNING id""",
             (project_access.project_id, prompt[:8000], model[:100], len(steps),
              sum(not s["ok"] for s in steps), rework_count(steps), json.dumps(segment_phases(steps)),
              session_id, project_access.project_id))
    if not row:
        raise ValueError("Chat session does not belong to the active project")
    trajectory_id = int(row["id"])
    for step in steps:
        exec_("""INSERT INTO trajectory_steps
                   (project_id, trajectory_id, ordinal, tool, action_family, args, summary, ok)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (project_access.project_id, trajectory_id, step["ordinal"], step["tool"], step["action_family"],
               json.dumps(step["args"]), step["summary"], step["ok"]))
    try:
        accepted = _submit(project_access, trajectory_id, prompt, steps)
    except Exception:  # noqa: BLE001 -- deterministic fallback remains queryable
        accepted = False
    if not accepted:
        _fallback(trajectory_id, steps, project_access.project_id, "Queued capacity exceeded")
    return trajectory_id

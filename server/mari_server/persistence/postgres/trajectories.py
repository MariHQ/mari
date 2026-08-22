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

from mari_server.providers import models as llm
from mari_server.identity import context as access
from mari_server.persistence.postgres.database import exec_, jload, q, q1
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


def list_trajectories(
    limit: int, offset: int, category: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    project_id = access.require_current_access().project_id
    args: list = [project_id]
    where = "project_id = %s"
    if category:
        where += " AND category = %s"
        args.append(category)
    args.extend((limit, offset))
    rows = q(f"""SELECT t.*,
                            COALESCE(t.promoted_workflow_id, t.observed_cluster_id,
                                     t.matched_workflow_id)
                              AS promoted_workflow_id,
                            aw.status AS promoted_workflow_status,
                            aw.name AS promoted_workflow_name,
                            aw.trajectory_id AS workflow_root_trajectory_id,
                            CASE WHEN aw.id IS NULL THEN 1 ELSE (
                              SELECT count(*) FROM trajectories member
                               WHERE member.project_id = t.project_id
                                 AND (member.observed_cluster_id = aw.id
                                      OR member.matched_workflow_id = aw.id
                                      OR member.promoted_workflow_id = aw.id)
                            ) END AS workflow_observation_count,
                            COALESCE(aw.cache_policy, 'none') AS promoted_workflow_cache_policy,
                            CASE
                              WHEN aw.id IS NULL OR aw.cache_policy = 'none' THEN 'disabled'
                              WHEN aw.cached_answer = '' THEN 'empty'
                              WHEN EXISTS (
                                SELECT 1
                                  FROM jsonb_array_elements(aw.cache_dependencies) dependency
                                  LEFT JOIN documents d
                                    ON d.project_id = aw.project_id
                                   AND d.id = (dependency->>'document_id')::int
                                 WHERE d.id IS NULL
                                    OR COALESCE(d.content_hash, '') <> COALESCE(dependency->>'content_hash', '')
                              ) THEN 'stale'
                              ELSE 'fresh'
                            END AS promoted_workflow_cache_state,
                            aw.cache_refreshed_at AS promoted_workflow_cache_refreshed_at,
                            jsonb_array_length(COALESCE(aw.cache_dependencies, '[]'))
                              AS promoted_workflow_dependency_count
                    FROM trajectories t
                    LEFT JOIN assistant_workflows aw
                      ON aw.project_id = t.project_id
                     AND aw.id = COALESCE(t.promoted_workflow_id, t.observed_cluster_id,
                                          t.matched_workflow_id)
                   WHERE {where.replace('project_id', 't.project_id')}
                   ORDER BY t.started_at DESC, t.id DESC LIMIT %s OFFSET %s""",
             tuple(args))
    if not rows:
        return [], [], []
    trajectory_ids = [row["id"] for row in rows]
    steps = q("""SELECT trajectory_id, ordinal, tool, action_family, args, summary, ok,
                          disposition, edited_args
                   FROM trajectory_steps WHERE project_id = %s AND trajectory_id = ANY(%s)
                   ORDER BY trajectory_id, ordinal""", (project_id, trajectory_ids))
    evidence = q("""SELECT trajectory_id, document_id, title, reason, rank, relevance, note
                      FROM trajectory_evidence
                     WHERE project_id = %s AND trajectory_id = ANY(%s)
                     ORDER BY trajectory_id, rank, id""", (project_id, trajectory_ids))
    return rows, steps, evidence


def trajectory_count(category: str | None = None) -> int:
    project_id = access.require_current_access().project_id
    if category:
        return int(q1("SELECT count(*) AS n FROM trajectories WHERE project_id = %s AND category = %s",
                      (project_id, category))["n"])
    return int(q1("SELECT count(*) AS n FROM trajectories WHERE project_id = %s", (project_id,))["n"])


def trajectory_categories() -> list[str]:
    project_id = access.require_current_access().project_id
    return [row["category"] for row in q(
        """SELECT category FROM trajectories WHERE project_id = %s
           GROUP BY category ORDER BY count(*) DESC, category""", (project_id,))]


def workflow_embedding_indexes(workflow_ids: list[int]) -> dict[int, dict]:
    if not workflow_ids:
        return {}
    project_id = access.require_current_access().project_id
    return {int(row["id"]): row for row in q(
        """SELECT id, name, match_index, embedding_profile
             FROM assistant_workflows
            WHERE project_id = %s AND id = ANY(%s)""",
        (project_id, list(dict.fromkeys(workflow_ids))),
    )}


def workflow_harvest_context(limit: int = 100) -> tuple[list[dict], list[dict]]:
    project_id = access.require_current_access().project_id
    observations = q(
        """SELECT id, prompt, macro_intent, category, execution_mode,
                  selected_workflow_id, selected_workflow_score, selected_workflow_exact,
                  COALESCE(promoted_workflow_id, observed_cluster_id,
                           matched_workflow_id) AS workflow_id
             FROM trajectories
            WHERE project_id = %s AND status IN ('ready', 'fallback')
            ORDER BY started_at DESC, id DESC LIMIT %s""",
        (project_id, max(10, min(int(limit), 200))),
    )
    workflows = q(
        """SELECT id, name, description FROM assistant_workflows
            WHERE project_id = %s AND status = 'active' ORDER BY updated_at DESC""",
        (project_id,),
    )
    return observations, workflows

FAMILY = {
    "search": "discover", "read_document": "inspect", "list_sources": "inspect",
    "list_flows": "inspect", "inspect_flow": "inspect",
    "list_workflow_observations": "inspect", "inspect_workflow_observation": "inspect",
    "list_product_surfaces": "inspect", "list_connector_types": "inspect",
    "list_tasks": "inspect", "list_answers": "inspect",
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


def harvest(session_id: int, prompt: str, trace: list[dict], model: str,
            selected_workflow_id: int | None = None, *, execution_mode: str = "generation",
            selected_workflow_score: float | None = None,
            selected_workflow_exact: bool = False,
            observed_cluster_id: int | None = None) -> int:
    project_access = access.require_current_access()
    _maybe_reconcile(project_access)
    steps = normalize_steps(trace)
    row = q1("""INSERT INTO trajectories
                  (project_id, session_id, prompt, status, model, step_count, failure_count,
                   rework_count, phases, matched_workflow_id, selected_workflow_id,
                   selected_workflow_score, selected_workflow_exact, execution_mode,
                   observed_cluster_id)
                SELECT %s, s.id, %s, 'processing', %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s
                  FROM chat_sessions s WHERE s.id = %s AND s.project_id = %s RETURNING id""",
             (project_access.project_id, prompt[:8000], model[:100], len(steps),
              sum(not s["ok"] for s in steps), rework_count(steps), json.dumps(segment_phases(steps)),
              observed_cluster_id, selected_workflow_id, selected_workflow_score,
              selected_workflow_exact, execution_mode[:40], observed_cluster_id,
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
    evidence: dict[int, dict] = {}
    for event in trace:
        for reference in event.get("evidence") or ():
            if not isinstance(reference, dict):
                continue
            try:
                document_id = int(reference.get("document_id"))
            except (TypeError, ValueError):
                continue
            evidence.setdefault(document_id, reference)
    for document_id, reference in evidence.items():
        exec_("""INSERT INTO trajectory_evidence
                   (project_id, trajectory_id, document_id, title, reason, rank)
                 SELECT %s, %s, d.id, %s, %s, %s FROM documents d
                  WHERE d.project_id = %s AND d.id = %s
                 ON CONFLICT (trajectory_id, document_id) DO UPDATE
                   SET reason = EXCLUDED.reason, rank = LEAST(trajectory_evidence.rank, EXCLUDED.rank)""",
              (project_access.project_id, trajectory_id,
               str(reference.get("title") or "")[:300],
               str(reference.get("reason") or "")[:300],
               max(0, int(reference.get("rank") or 0)),
               project_access.project_id, document_id))
    try:
        accepted = _submit(project_access, trajectory_id, prompt, steps)
    except Exception:  # noqa: BLE001 -- deterministic fallback remains queryable
        accepted = False
    if not accepted:
        _fallback(trajectory_id, steps, project_access.project_id, "Queued capacity exceeded")
    return trajectory_id


def record_external_observation(prompt: str, trace: list[dict], model: str,
                                selected_workflow_id: int | None = None, *,
                                execution_mode: str = "generation",
                                selected_workflow_score: float | None = None,
                                selected_workflow_exact: bool = False,
                                observed_cluster_id: int | None = None) -> int:
    """Record an already-completed destination turn without another model call."""
    project_id = access.require_current_access().project_id
    steps = normalize_steps(trace)
    summary = "; ".join(step["summary"] for step in steps if step.get("summary"))[:2000]
    row = q1(
        """INSERT INTO trajectories
              (project_id, session_id, prompt, status, model, layer1, layer2, category,
               macro_intent, step_count, failure_count, rework_count, phases,
               matched_workflow_id, selected_workflow_id, selected_workflow_score,
               selected_workflow_exact, execution_mode, observed_cluster_id, completed_at)
            VALUES (%s, NULL, %s, 'ready', %s, %s, %s, 'Assistant conversations',
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
        (project_id, prompt[:8000], model[:100], summary, prompt[:500], prompt[:300],
         len(steps), sum(not step["ok"] for step in steps), rework_count(steps),
         json.dumps(segment_phases(steps)), observed_cluster_id, selected_workflow_id,
         selected_workflow_score, selected_workflow_exact, execution_mode[:40],
         observed_cluster_id),
    )
    trajectory_id = int(row["id"])
    for step in steps:
        exec_("""INSERT INTO trajectory_steps
                   (project_id, trajectory_id, ordinal, tool, action_family, args, summary, ok)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (project_id, trajectory_id, step["ordinal"], step["tool"],
               step["action_family"], json.dumps(step["args"]), step["summary"], step["ok"]))
    for event in trace:
        for reference in event.get("evidence") or ():
            try:
                document_id = int(reference.get("document_id"))
            except (AttributeError, TypeError, ValueError):
                continue
            exec_("""INSERT INTO trajectory_evidence
                       (project_id, trajectory_id, document_id, title, reason, rank)
                     SELECT %s, %s, d.id, %s, %s, %s FROM documents d
                      WHERE d.project_id = %s AND d.id = %s
                     ON CONFLICT (trajectory_id, document_id) DO NOTHING""",
                  (project_id, trajectory_id, str(reference.get("title") or "")[:300],
                   str(reference.get("reason") or "")[:300],
                   max(0, int(reference.get("rank") or 0)), project_id, document_id))
    return trajectory_id


def tune_step(trajectory_id: int, ordinal: int, disposition: str, edited_args: dict | None) -> bool:
    if disposition not in {"included", "excluded", "preferred"}:
        raise ValueError("Tool disposition must be included, excluded, or preferred.")
    project_id = access.require_current_access().project_id
    return bool(q1("""UPDATE trajectory_steps SET disposition = %s, edited_args = %s
                       WHERE project_id = %s AND trajectory_id = %s AND ordinal = %s
                       RETURNING id""",
                   (disposition, json.dumps(edited_args) if edited_args is not None else None,
                    project_id, trajectory_id, ordinal)))


def tune_evidence(trajectory_id: int, document_id: int, relevance: str, note: str) -> bool:
    if relevance not in {"observed", "relevant", "irrelevant", "pinned"}:
        raise ValueError("Evidence relevance is not recognized.")
    project_id = access.require_current_access().project_id
    return bool(q1("""UPDATE trajectory_evidence SET relevance = %s, note = %s
                       WHERE project_id = %s AND trajectory_id = %s AND document_id = %s
                       RETURNING id""",
                   (relevance, note.strip()[:500], project_id, trajectory_id, document_id)))


def promote_to_workflow(trajectory_id: int, name: str, *, force_new: bool = False,
                        matched_workflow_id: int | None = None) -> int:
    """Codify a human-tuned trace as active assistant guidance."""
    project_id = access.require_current_access().project_id
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Workflow name is required.")

    def promote(conn):
        row = conn.execute(
            """SELECT id, layer2, macro_intent, phases, promoted_workflow_id,
                      matched_workflow_id FROM trajectories
                 WHERE project_id = %s AND id = %s FOR UPDATE""",
            (project_id, trajectory_id),
        ).fetchone()
        if not row:
            raise ValueError("Trajectory not found.")
        if row.get("promoted_workflow_id") and not force_new:
            return int(row["promoted_workflow_id"])
        existing_match = row.get("matched_workflow_id") or matched_workflow_id
        if existing_match and not force_new:
            workflow_id = int(existing_match)
            owned = conn.execute(
                "SELECT id FROM assistant_workflows WHERE project_id = %s AND id = %s",
                (project_id, workflow_id),
            ).fetchone()
            if not owned:
                raise ValueError("Matched workflow not found.")
            conn.execute(
                """UPDATE trajectories SET promoted_workflow_id = %s, matched_workflow_id = %s
                     WHERE project_id = %s AND id = %s""",
                (workflow_id, workflow_id, project_id, trajectory_id),
            )
            return workflow_id
        if conn.execute(
            "SELECT 1 FROM assistant_workflows WHERE project_id = %s AND name = %s",
            (project_id, clean_name),
        ).fetchone():
            raise ValueError("A workflow with that name already exists.")
        observed = conn.execute(
            """SELECT ordinal, tool, action_family, args, edited_args, disposition, summary
                 FROM trajectory_steps
                WHERE project_id = %s AND trajectory_id = %s AND disposition <> 'excluded'
                ORDER BY ordinal""", (project_id, trajectory_id),
        ).fetchall()
        if not observed:
            raise ValueError("Include at least one tool call before creating a workflow.")
        steps = [{
                "ordinal": int(step["ordinal"]),
                "tool": str(step["tool"]),
                "arguments": jload(step.get("edited_args")) if step.get("edited_args") is not None
                else (jload(step.get("args")) or {}),
                "family": str(step["action_family"]),
                "disposition": str(step["disposition"]),
                "summary": str(step.get("summary") or ""),
        } for step in observed]
        workflow = conn.execute(
            """INSERT INTO assistant_workflows
                 (project_id, trajectory_id, name, description, status, steps, phases)
               VALUES (%s, %s, %s, %s, 'active', %s, %s)
               RETURNING id""",
            (project_id, trajectory_id, clean_name,
             str(row.get("layer2") or row.get("macro_intent") or "Observed agent workflow")[:500],
             json.dumps(steps), json.dumps(jload(row.get("phases")) or [])),
        ).fetchone()
        workflow_id = int(workflow["id"])
        conn.execute(
            """UPDATE trajectories
                  SET promoted_workflow_id = %s, matched_workflow_id = %s
                WHERE project_id = %s AND id = %s""",
            (workflow_id, workflow_id, project_id, trajectory_id),
        )
        return workflow_id

    from mari_server.persistence.postgres.database import transaction
    return transaction(promote)


def trajectory_for_split(trajectory_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    return q1("""SELECT id, prompt, layer2, macro_intent,
                        COALESCE(promoted_workflow_id, observed_cluster_id,
                                 matched_workflow_id) AS workflow_id
                   FROM trajectories WHERE project_id = %s AND id = %s""",
              (project_id, trajectory_id))


def unassigned_trajectories(limit: int = 200) -> list[dict]:
    project_id = access.require_current_access().project_id
    return q("""SELECT id, prompt FROM trajectories
                 WHERE project_id = %s AND promoted_workflow_id IS NULL
                   AND observed_cluster_id IS NULL AND matched_workflow_id IS NULL
                   AND status IN ('ready', 'fallback')
                 ORDER BY started_at DESC, id DESC LIMIT %s""",
             (project_id, max(1, min(int(limit), 500))))


def assign_trajectory_cluster(trajectory_id: int, workflow_id: int) -> bool:
    project_id = access.require_current_access().project_id
    return bool(q1("""UPDATE trajectories t
                          SET observed_cluster_id = %s, matched_workflow_id = %s
                        WHERE t.project_id = %s AND t.id = %s
                          AND t.promoted_workflow_id IS NULL
                          AND t.matched_workflow_id IS NULL
                          AND EXISTS (SELECT 1 FROM assistant_workflows aw
                                       WHERE aw.project_id = t.project_id AND aw.id = %s)
                      RETURNING t.id""",
                   (workflow_id, workflow_id, project_id, trajectory_id, workflow_id)))


def split_workflow(trajectory_id: int, name: str) -> int:
    row = trajectory_for_split(trajectory_id)
    if not row or not row.get("workflow_id"):
        raise ValueError("Only an observation already in a workflow can be split.")
    return promote_to_workflow(trajectory_id, name, force_new=True)


def set_workflow_enabled(workflow_id: int, enabled: bool) -> bool:
    project_id = access.require_current_access().project_id
    return bool(q1(
        """UPDATE assistant_workflows SET status = %s, updated_at = now()
              WHERE project_id = %s AND id = %s RETURNING id""",
        ("active" if enabled else "paused", project_id, workflow_id),
    ))


def delete_workflow(workflow_id: int) -> bool:
    """Delete the codified workflow while retaining its observed trajectories."""
    project_id = access.require_current_access().project_id

    def delete(conn):
        row = conn.execute(
            """SELECT id FROM assistant_workflows
                 WHERE project_id = %s AND id = %s FOR UPDATE""",
            (project_id, workflow_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """UPDATE trajectories
                  SET promoted_workflow_id = NULL, matched_workflow_id = NULL
                WHERE project_id = %s
                  AND (promoted_workflow_id = %s OR matched_workflow_id = %s)""",
            (project_id, workflow_id, workflow_id),
        )
        return bool(conn.execute(
            """DELETE FROM assistant_workflows
                 WHERE project_id = %s AND id = %s RETURNING id""",
            (project_id, workflow_id),
        ).fetchone())

    from mari_server.persistence.postgres.database import transaction
    return bool(transaction(delete))


def active_workflows(limit: int = 20) -> list[dict]:
    project_id = access.require_current_access().project_id
    return q(
        """SELECT aw.id, aw.trajectory_id, aw.name, aw.description, aw.steps, aw.phases,
                  aw.match_index, aw.embedding_profile, aw.match_threshold, aw.cache_policy,
                  aw.cached_answer, aw.cached_sources, aw.cache_dependencies,
                  aw.cache_refreshed_at, t.prompt AS trajectory_prompt
             FROM assistant_workflows aw JOIN trajectories t
               ON t.project_id = aw.project_id AND t.id = aw.trajectory_id
            WHERE aw.project_id = %s AND aw.status = 'active'
            ORDER BY aw.updated_at DESC, aw.id DESC LIMIT %s""",
        (project_id, max(1, min(int(limit), 50))),
    )


def workflow_cache_state(workflow: dict) -> str:
    if workflow.get("cache_policy") != "reviewed_answer":
        return "disabled"
    if not str(workflow.get("cached_answer") or ""):
        return "empty"
    dependencies = jload(workflow.get("cache_dependencies")) or []
    if not dependencies:
        return "fresh"
    project_id = access.require_current_access().project_id
    document_ids = [int(row["document_id"]) for row in dependencies]
    current = {int(row["id"]): str(row.get("content_hash") or "") for row in q(
        "SELECT id, content_hash FROM documents WHERE project_id = %s AND id = ANY(%s)",
        (project_id, document_ids),
    )}
    return "stale" if any(
        current.get(int(row["document_id"])) != str(row.get("content_hash") or "")
        for row in dependencies
    ) else "fresh"


def cached_response(workflow: dict) -> dict | None:
    if workflow_cache_state(workflow) != "fresh":
        return None
    return {
        "answer": str(workflow.get("cached_answer") or ""),
        "sources": jload(workflow.get("cached_sources")) or [],
        "refreshed_at": workflow.get("cache_refreshed_at"),
    }


def _dependencies(conn, project_id: int, document_ids: list[int]) -> list[dict]:
    if not document_ids:
        return []
    return [{"document_id": int(row["id"]), "content_hash": str(row.get("content_hash") or "")}
            for row in conn.execute(
                """SELECT id, content_hash FROM documents
                     WHERE project_id = %s AND id = ANY(%s) ORDER BY id""",
                (project_id, list(dict.fromkeys(document_ids))),
            ).fetchall()]


def _source_snapshots(conn, project_id: int, document_ids: list[int]) -> list[dict]:
    if not document_ids:
        return []
    return [{
        "n": index, "document_id": int(row["id"]), "title": str(row["title"]),
        "source": str(row["source"]), "href": f"/knowledge/doc?id={row['id']}",
    } for index, row in enumerate(conn.execute(
        """SELECT id, title, source FROM documents
             WHERE project_id = %s AND id = ANY(%s)
             ORDER BY array_position(%s::int[], id)""",
        (project_id, list(dict.fromkeys(document_ids)), list(dict.fromkeys(document_ids))),
    ).fetchall(), 1)]


def configure_workflow_cache(workflow_id: int, enabled: bool) -> bool:
    """Explicitly enable a reviewed-response cache or remove it completely."""
    project_id = access.require_current_access().project_id

    def configure(conn):
        workflow = conn.execute(
            """SELECT id, trajectory_id FROM assistant_workflows
                 WHERE project_id = %s AND id = %s FOR UPDATE""",
            (project_id, workflow_id),
        ).fetchone()
        if not workflow:
            return False
        if not enabled:
            conn.execute(
                """UPDATE assistant_workflows SET cache_policy = 'none', cached_answer = '',
                          cached_sources = '[]', cache_dependencies = '[]',
                          cache_refreshed_at = NULL, updated_at = now()
                     WHERE project_id = %s AND id = %s""",
                (project_id, workflow_id),
            )
            return True
        trajectory = conn.execute(
            "SELECT session_id FROM trajectories WHERE project_id = %s AND id = %s",
            (project_id, workflow["trajectory_id"]),
        ).fetchone()
        message = None
        if trajectory and trajectory.get("session_id"):
            message = conn.execute(
                """SELECT content FROM chat_messages
                     WHERE project_id = %s AND session_id = %s AND role = 'assistant'
                     ORDER BY id DESC LIMIT 1""",
                (project_id, trajectory["session_id"]),
            ).fetchone()
        if not message or not str(message.get("content") or "").strip():
            raise ValueError("This workflow has no reviewed answer to cache. Reconcile it first.")
        evidence = conn.execute(
            """SELECT document_id FROM trajectory_evidence
                 WHERE project_id = %s AND trajectory_id = %s AND relevance <> 'irrelevant'
                 ORDER BY rank, id""",
            (project_id, workflow["trajectory_id"]),
        ).fetchall()
        document_ids = [int(row["document_id"]) for row in evidence]
        dependencies = _dependencies(conn, project_id, document_ids)
        if not dependencies:
            raise ValueError("This workflow has no tracked documents to validate.")
        sources = _source_snapshots(conn, project_id, document_ids)
        conn.execute(
            """UPDATE assistant_workflows
                  SET cache_policy = 'reviewed_answer', cached_answer = %s,
                      cached_sources = %s, cache_dependencies = %s,
                      cache_refreshed_at = now(), updated_at = now()
                WHERE project_id = %s AND id = %s""",
            (str(message["content"]), json.dumps(sources),
             json.dumps(dependencies), project_id, workflow_id),
        )
        return True

    from mari_server.persistence.postgres.database import transaction
    return bool(transaction(configure))


def workflow_for_reconcile(workflow_id: int) -> dict | None:
    project_id = access.require_current_access().project_id
    return q1(
        """SELECT aw.*, t.prompt
             FROM assistant_workflows aw JOIN trajectories t
               ON t.project_id = aw.project_id AND t.id = aw.trajectory_id
            WHERE aw.project_id = %s AND aw.id = %s""",
        (project_id, workflow_id),
    )


def stale_workflow_ids(limit: int = 50) -> list[int]:
    project_id = access.require_current_access().project_id
    rows = q(
        """SELECT aw.id FROM assistant_workflows aw
            WHERE aw.project_id = %s AND aw.cache_policy = 'reviewed_answer'
              AND (aw.cached_answer = '' OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(aw.cache_dependencies) dependency
                LEFT JOIN documents d ON d.project_id = aw.project_id
                  AND d.id = (dependency->>'document_id')::int
                WHERE d.id IS NULL OR COALESCE(d.content_hash, '') <>
                  COALESCE(dependency->>'content_hash', '')
              ))
            ORDER BY aw.updated_at, aw.id LIMIT %s""",
        (project_id, max(1, min(int(limit), 100))),
    )
    return [int(row["id"]) for row in rows]


def save_workflow_cache(workflow_id: int, answer: str, sources: list[dict],
                        document_ids: list[int]) -> bool:
    project_id = access.require_current_access().project_id

    def save(conn):
        dependencies = _dependencies(conn, project_id, document_ids)
        return bool(conn.execute(
            """UPDATE assistant_workflows
                  SET cached_answer = %s, cached_sources = %s, cache_dependencies = %s,
                      cache_refreshed_at = now(), updated_at = now()
                WHERE project_id = %s AND id = %s
                  AND cache_policy = 'reviewed_answer' RETURNING id""",
            (answer, json.dumps(sources), json.dumps(dependencies), project_id, workflow_id),
        ).fetchone())

    from mari_server.persistence.postgres.database import transaction
    return bool(transaction(save))


def save_match_index(workflow_id: int, profile: str, value: dict) -> None:
    project_id = access.require_current_access().project_id
    exec_(
        """UPDATE assistant_workflows
              SET match_index = %s, embedding_profile = %s, updated_at = now()
              WHERE project_id = %s AND id = %s""",
        (json.dumps(value), profile, project_id, workflow_id),
    )

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
from mari_server.persistence.postgres.search import like_pattern
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

# The card shows the promoted workflow inline (name, status, node count) rather
# than navigating away to it, so the promotion is read with the trajectory it
# came from instead of by a second round trip per card.
_PROMOTED_JOIN = ("LEFT JOIN workflows w ON w.id = t.promoted_workflow_id "
                  "AND w.project_id = t.project_id")
_SELECT = ("t.*, w.name AS promoted_workflow_name, w.status AS promoted_workflow_status, "
           "COALESCE(jsonb_array_length(w.nodes), 0) AS promoted_workflow_nodes")

#: What the Observed tab's failure filter accepts. Anything else is ignored
#: rather than refused: a stale bookmark narrows to nothing it can explain.
_FAILURE_FILTERS = {"with", "none"}


def _filter_clause(category: str | None, status: str | None,
                   failures: str | None, search: str | None) -> tuple[str, list]:
    """The Observed tab's filters, as SQL. The caller supplies project_id first.

    Filtering here rather than in the page is what keeps "Showing 1-25 of N"
    honest: a client-side filter over one page of 25 would narrow the rows and
    leave the total describing a different set.
    """
    where = ["t.project_id = %s"]
    args: list = []
    if category:
        where.append("t.category = %s")
        args.append(category)
    if status:
        where.append("t.status = %s")
        args.append(status)
    if failures in _FAILURE_FILTERS:
        where.append("t.failure_count > 0" if failures == "with" else "t.failure_count = 0")
    if search:
        where.append("(t.prompt ILIKE %s OR t.macro_intent ILIKE %s"
                     " OR t.layer2 ILIKE %s OR t.category ILIKE %s)")
        args.extend([like_pattern(search[:120])] * 4)
    return " AND ".join(where), args


def _details(project_id: int, trajectory_ids: list[int]) -> tuple[list[dict], list[dict]]:
    if not trajectory_ids:
        return [], []
    steps = q("""SELECT trajectory_id, ordinal, tool, action_family, args, summary, ok,
                          disposition, edited_args
                   FROM trajectory_steps WHERE project_id = %s AND trajectory_id = ANY(%s)
                   ORDER BY trajectory_id, ordinal""", (project_id, trajectory_ids))
    evidence = q("""SELECT trajectory_id, document_id, title, reason, rank, relevance, note
                      FROM trajectory_evidence
                     WHERE project_id = %s AND trajectory_id = ANY(%s)
                     ORDER BY trajectory_id, rank, id""", (project_id, trajectory_ids))
    return steps, evidence


def list_trajectories(
    limit: int, offset: int, category: str | None = None, status: str | None = None,
    failures: str | None = None, search: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    project_id = access.require_current_access().project_id
    where, args = _filter_clause(category, status, failures, search)
    rows = q(f"""SELECT {_SELECT} FROM trajectories t {_PROMOTED_JOIN}
                  WHERE {where} ORDER BY t.started_at DESC, t.id DESC LIMIT %s OFFSET %s""",
             (project_id, *args, limit, offset))
    return (rows, *_details(project_id, [row["id"] for row in rows]))


def get_trajectory(trajectory_id: int) -> tuple[list[dict], list[dict], list[dict]]:
    """One observed workflow by id, in the same shape the list returns.

    The Observed tab deep-links a single workflow into its drawer (an approved
    answer links back to the workflow it was promoted from), and that workflow
    is very often not on the page the reader happens to be looking at. Reading
    it directly is what makes the link land on the workflow rather than on a
    filtered list that does not contain it."""
    project_id = access.require_current_access().project_id
    rows = q(f"""SELECT {_SELECT} FROM trajectories t {_PROMOTED_JOIN}
                  WHERE t.project_id = %s AND t.id = %s""", (project_id, trajectory_id))
    return (rows, *_details(project_id, [row["id"] for row in rows]))


def trajectory_count(category: str | None = None, status: str | None = None,
                     failures: str | None = None, search: str | None = None) -> int:
    project_id = access.require_current_access().project_id
    where, args = _filter_clause(category, status, failures, search)
    return int(q1(f"SELECT count(*) AS n FROM trajectories t WHERE {where}",
                  (project_id, *args))["n"])


def trajectory_statuses() -> list[str]:
    project_id = access.require_current_access().project_id
    return [row["status"] for row in q(
        """SELECT status FROM trajectories WHERE project_id = %s
           GROUP BY status ORDER BY count(*) DESC, status""", (project_id,))]


def trajectory_categories() -> list[str]:
    project_id = access.require_current_access().project_id
    return [row["category"] for row in q(
        """SELECT category FROM trajectories WHERE project_id = %s
           GROUP BY category ORDER BY count(*) DESC, category""", (project_id,))]

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


def promote_to_workflow(trajectory_id: int, name: str) -> dict:
    """Create a paused, editable workflow from the human-tuned trace.

    Answers with the workflow itself, not just its id: the card shows what
    promotion produced (name, status, how many nodes) in place, and the node
    count is the part worth showing — an excluded tool is not in the workflow,
    so the count is how tuning becomes visible.
    """
    project_id = access.require_current_access().project_id
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Workflow name is required.")

    def promote(conn):
        row = conn.execute(
            """SELECT id, layer2, macro_intent, promoted_workflow_id FROM trajectories
                 WHERE project_id = %s AND id = %s FOR UPDATE""",
            (project_id, trajectory_id),
        ).fetchone()
        if not row:
            raise ValueError("Trajectory not found.")
        if row.get("promoted_workflow_id"):
            existing = conn.execute(
                """SELECT id, name, status, COALESCE(jsonb_array_length(nodes), 0) AS node_count
                     FROM workflows WHERE project_id = %s AND id = %s""",
                (project_id, row["promoted_workflow_id"]),
            ).fetchone()
            if existing:
                return dict(existing)
        if conn.execute(
            "SELECT 1 FROM workflows WHERE project_id = %s AND name = %s", (project_id, clean_name),
        ).fetchone():
            raise ValueError("A workflow with that name already exists.")
        observed = conn.execute(
            """SELECT ordinal, tool, action_family, args, edited_args, disposition
                 FROM trajectory_steps
                WHERE project_id = %s AND trajectory_id = %s AND disposition <> 'excluded'
                ORDER BY ordinal""", (project_id, trajectory_id),
        ).fetchall()
        if not observed:
            raise ValueError("Include at least one tool call before creating a workflow.")
        nodes = [{"kind": "trigger", "label": "Manual", "config": {"label": "Human approved"}}]
        nodes.extend({
            "kind": "observed_tool", "label": str(step["tool"]),
            "config": {
                "tool": str(step["tool"]),
                "arguments": jload(step.get("edited_args")) if step.get("edited_args") is not None
                else (jload(step.get("args")) or {}),
                "family": str(step["action_family"]),
                "disposition": str(step["disposition"]),
            },
        } for step in observed)
        workflow = conn.execute(
            """INSERT INTO workflows
                 (project_id, name, description, color, pinned, status, nodes, trigger)
               VALUES (%s, %s, %s, '#5f6f52', false, 'paused', %s, '{"on":""}'::jsonb)
               RETURNING id""",
            (project_id, clean_name,
             str(row.get("layer2") or row.get("macro_intent") or "Observed agent workflow")[:500],
             json.dumps(nodes)),
        ).fetchone()
        workflow_id = int(workflow["id"])
        conn.execute(
            "UPDATE trajectories SET promoted_workflow_id = %s WHERE project_id = %s AND id = %s",
            (workflow_id, project_id, trajectory_id),
        )
        return {"id": workflow_id, "name": clean_name, "status": "paused",
                "node_count": len(nodes)}

    from mari_server.persistence.postgres.database import transaction
    return transaction(promote)


def set_disposition(trajectory_id: int, disposition: str) -> bool:
    """Turn an observed workflow down without deleting the evidence for it.

    A rejected workflow stays queryable: the point of harvesting a trace is to
    learn what the agent did, and deleting every trace somebody disagreed with
    throws that away. Deletion is a separate, confirmed action.
    """
    if disposition not in {"observed", "rejected"}:
        raise ValueError("Workflow disposition must be observed or rejected.")
    project_id = access.require_current_access().project_id
    return bool(q1("""UPDATE trajectories SET disposition = %s
                       WHERE project_id = %s AND id = %s RETURNING id""",
                   (disposition, project_id, trajectory_id)))


def delete_trajectory(trajectory_id: int) -> bool:
    """Remove an observed workflow and everything harvested with it.

    Steps and evidence are cascaded by their foreign keys, and they are deleted
    explicitly first so a legacy row whose constraint predates the cascade
    cannot leave orphans behind.
    """
    project_id = access.require_current_access().project_id

    def remove(conn):
        exists = conn.execute(
            "SELECT id FROM trajectories WHERE project_id = %s AND id = %s FOR UPDATE",
            (project_id, trajectory_id),
        ).fetchone()
        if not exists:
            return False
        conn.execute("DELETE FROM trajectory_evidence WHERE project_id = %s AND trajectory_id = %s",
                     (project_id, trajectory_id))
        conn.execute("DELETE FROM trajectory_steps WHERE project_id = %s AND trajectory_id = %s",
                     (project_id, trajectory_id))
        conn.execute("DELETE FROM trajectories WHERE project_id = %s AND id = %s",
                     (project_id, trajectory_id))
        return True

    from mari_server.persistence.postgres.database import transaction
    return transaction(remove)


#: How long a promoted draft runs before the console asks somebody to look at
#: it again. An answer mined from one conversation is the most perishable thing
#: in the library, so it carries a recheck date from the moment it is drafted.
_RECHECK_DAYS = 90


def promote_to_answer(trajectory_id: int, owner: str) -> int:
    """Draft an approved answer from what the agent actually answered.

    The question is what the person asked, the wording is the answer the agent
    gave, and the sources are the documents the run cited. It lands as a DRAFT:
    nothing a bot serves changes until a human approves it, which is the whole
    contract of the Approved answers tab.
    """
    project_id = access.require_current_access().project_id

    def promote(conn):
        row = conn.execute(
            """SELECT id, session_id, prompt, macro_intent FROM trajectories
                 WHERE project_id = %s AND id = %s FOR UPDATE""",
            (project_id, trajectory_id),
        ).fetchone()
        if not row:
            raise ValueError("Workflow not found.")
        question = str(row.get("prompt") or row.get("macro_intent") or "").strip()[:200]
        if not question:
            raise ValueError("This workflow has no question to promote.")
        answered = conn.execute(
            """SELECT content FROM chat_messages
                 WHERE project_id = %s AND session_id = %s AND role = 'assistant'
                 ORDER BY id DESC LIMIT 1""",
            (project_id, row.get("session_id")),
        ).fetchone() if row.get("session_id") else None
        answer = str((answered or {}).get("content") or "").strip()
        if not answer:
            raise ValueError("This workflow has no answer to promote yet.")
        sources = [
            {"source": str(reference["source"] or "mari"), "title": str(reference["title"] or "")}
            for reference in conn.execute(
                """SELECT COALESCE(d.source, '') AS source,
                          COALESCE(NULLIF(e.title, ''), d.title, '') AS title
                     FROM trajectory_evidence e LEFT JOIN documents d ON d.id = e.document_id
                    WHERE e.project_id = %s AND e.trajectory_id = %s
                      AND e.relevance <> 'irrelevant'
                    ORDER BY (e.relevance = 'pinned') DESC, e.rank, e.id LIMIT 8""",
                (project_id, trajectory_id),
            ).fetchall()
        ]
        # An earlier promotion of the same workflow under a different question:
        # the new draft replaces it rather than sitting beside it unexplained.
        previous = conn.execute(
            """SELECT id FROM approved_answers
                 WHERE project_id = %s AND trajectory_id = %s AND question <> %s
                 ORDER BY id DESC LIMIT 1""",
            (project_id, trajectory_id, question),
        ).fetchone()
        existing = conn.execute(
            "SELECT id, status FROM approved_answers WHERE project_id = %s AND question = %s",
            (project_id, question),
        ).fetchone()
        if existing and str(existing["status"]) != "draft":
            # Never overwrite wording bots are already serving, or wording
            # somebody deliberately retired.
            raise ValueError(
                f"An answer for that question is already {existing['status']}. "
                "Edit it in Approved answers instead.")
        recheck = f"{_RECHECK_DAYS} days"
        if existing:
            conn.execute(
                """UPDATE approved_answers
                      SET answer = %s, sources = %s, trajectory_id = %s, supersedes = %s,
                          owner_name = %s, recheck_after = now() + %s::interval, updated = now()
                    WHERE project_id = %s AND id = %s""",
                (answer, json.dumps(sources), trajectory_id,
                 previous["id"] if previous else None, owner[:120], recheck,
                 project_id, existing["id"]),
            )
            return int(existing["id"])
        created = conn.execute(
            """INSERT INTO approved_answers
                 (project_id, question, answer, status, owner_name, sources,
                  trajectory_id, supersedes, recheck_after, updated)
               VALUES (%s, %s, %s, 'draft', %s, %s, %s, %s, now() + %s::interval, now())
               RETURNING id""",
            (project_id, question, answer, owner[:120], json.dumps(sources),
             trajectory_id, previous["id"] if previous else None, recheck),
        ).fetchone()
        return int(created["id"])

    from mari_server.persistence.postgres.database import transaction
    return transaction(promote)


def tool_preferences(category: str | None = None) -> dict[str, list[str]]:
    """Which tools reviewers marked preferred, and which they marked excluded.

    The Workflows page grades a run's individual tool calls. This is the read
    that makes those grades matter: the planner sees preferred tools first and
    never sees an excluded one. A tool is preferred when it was marked so more
    often than it was excluded, and excluded when the reverse holds; a tool
    people disagree about stays where it was.
    """
    project_id = access.require_current_access().project_id
    rows = q("""SELECT s.tool,
                       count(*) FILTER (WHERE s.disposition = 'preferred') AS preferred,
                       count(*) FILTER (WHERE s.disposition = 'excluded') AS excluded
                  FROM trajectory_steps s
                  JOIN trajectories t ON t.id = s.trajectory_id AND t.project_id = s.project_id
                 WHERE s.project_id = %s AND s.disposition <> 'included'
                   AND t.disposition <> 'rejected'
                   AND (%s::text IS NULL OR t.category = %s)
                 GROUP BY s.tool""", (project_id, category, category))
    preferred = [str(row["tool"]) for row in rows if int(row["preferred"]) > int(row["excluded"])]
    excluded = [str(row["tool"]) for row in rows if int(row["excluded"]) > int(row["preferred"])]
    return {"preferred": sorted(preferred), "excluded": sorted(excluded)}


def pinned_document_ids(limit: int = 200) -> set[int]:
    """Documents a reviewer pinned as the evidence an answer should rest on.

    The Workflows page lets somebody mark a document "pinned" against the run
    that cited it. Retrieval still decides WHETHER a document is relevant to a
    new question; this only decides that, among the documents it did find, a
    pinned one is worth putting in front of the model rather than cutting.
    """
    project_id = access.require_current_access().project_id
    return {int(row["document_id"]) for row in q(
        """SELECT DISTINCT document_id FROM trajectory_evidence
            WHERE project_id = %s AND relevance = 'pinned' LIMIT %s""",
        (project_id, max(1, min(int(limit), 1000))))}

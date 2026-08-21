"""Mari — flow execution engine (FLOWS-DESIGN.md).

Executes a workflow's step pipeline for real: each step maps to an existing
platform primitive (hybrid search, LLM refine, fact check, tagging, link
derivation, site deploy, tasks, notifications). Runs execute on a background
thread; per-step status/duration persists to workflow_runs.rows_data so the UI
can poll live. Approval steps pause the run ('waiting'); approveRun resumes it.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import threading
import time
import typing as t

from mari_server.providers import models as llm
from mari_server import settings as config
from mari_server.identity import context as access
from mari_server.persistence.postgres import workflows as workflow_store
from mari_components.workflow_runtime import matching_documents, run_step


def _now_label() -> str:
    return time.strftime("%b %d, %I:%M %p")


def _elapsed(start: float) -> str:
    s = int(time.time() - start)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ————— step implementations — each returns (status, detail, ctx_updates) —————


# `fetch_docs` may rotate instead of always taking the newest. A scan flow that
# takes the newest k every time reads the same k documents forever (FACT-2) —
# the fix has to reach the step that picks them, not only the scan that reads
# them. The value names the scanner whose bookkeeping column to order by; the
# column names are fixed here rather than taken from config, because this
# interpolates into SQL.
def _step_fetch(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    query, tag, k = cfg.get("query", ""), cfg.get("tag", ""), max(1, int(cfg.get("k", 3)))
    rotation = str(cfg.get("rotate") or "")
    trigger_ids = ctx.get("trigger_doc_ids") or []
    rows = workflow_store.select_documents(
        trigger_ids=trigger_ids, tag=tag, query=query, limit=k, rotation=rotation,
    )
    ids = [r["id"] for r in rows]
    names = ", ".join(r["title"][:40] for r in rows[:3])
    src = " (from trigger)" if trigger_ids else (" (least recently scanned)" if rotation else "")
    return "passed", f"{len(ids)} documents{src} · {names}", {"doc_ids": ids}


def _step_refine(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    # late imports — flowengine must stay importable before app/db load
    from mari_server.knowledge.service import llm_refine
    skill = cfg.get("skill", "tighten")
    total = 0
    for doc_id in ctx.get("doc_ids", [])[:2]:  # cap LLM work per run
        doc = workflow_store.document(doc_id)
        if not doc:
            continue
        total += workflow_store.save_suggested_changes(doc_id, llm_refine(doc, skill))
    return "passed", f"{total} edits suggested ({skill})", {"edits": total}


def _step_fact_check(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.knowledge.service import fact_check_document
    contradictions = 0
    checked = 0
    for doc_id in ctx.get("doc_ids", [])[:2]:
        contradictions += fact_check_document(doc_id)
        checked += 1
    detail = f"{checked} docs checked · {contradictions} contradiction{'s' if contradictions != 1 else ''}"
    return "passed", detail, {"contradictions": contradictions}


def _step_condition(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    field = cfg.get("field", "contradictions")
    value = int(ctx.get(field, 0))
    threshold = int(cfg.get("greater_than", 0))
    taken = value > threshold
    return "passed", f"{field} = {value} → branch {'taken' if taken else 'skipped'}", {"branch_taken": taken}


def _step_tag(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    tag = cfg.get("tag", "needs-review")
    if ctx.get("dry_run"):
        return "passed", f"would tag {len(ctx.get('doc_ids', []))} docs '{tag}' (dry run)", {}
    # One transaction for the whole loop (FLOW-3), stated rather than inherited:
    # this used to rely on psycopg's connection-level implicit transaction, which
    # is correct but invisible — a reader checking whether a mid-loop failure
    # leaves half the documents tagged had to know that rule to answer. The
    # explicit block says it, and it keeps saying it if the loop later grows a
    # second statement or an early return.
    n = workflow_store.tag_documents(ctx.get("doc_ids", []), tag)
    return "passed", f"tagged {n} docs '{tag}'", {}


def _step_derive_links(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.knowledge.service import derive_links
    added = derive_links()
    return "passed", f"{added} new semantic links", {"links": added}


def _step_create_task(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    title = cfg.get("title", "Review flow output")
    assignee = str(cfg.get("assignee") or "").strip()
    if ctx.get("branch_taken") is False and cfg.get("only_if_branch"):
        return "skipped", "branch not taken", {}
    if ctx.get("dry_run"):
        return "passed", f"would create {'assigned' if assignee else 'unassigned'} task: {title[:60]} (dry run)", {}
    workflow_store.create_review_task(
        title, assignee, cfg.get("kind", "factcheck"), cfg.get("kind_label", "Fact check"),
    )
    return "passed", f"task: {title[:60]}", {}


def _step_approval(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    assignee = str(cfg.get("assignee") or "").strip()
    if not assignee:
        return "failed", "approval step requires an approver", {}
    if ctx.get("dry_run"):
        return "passed", f"would await {assignee} (dry run)", {}
    return "waiting", f"awaiting {assignee}", {"pause": True}


def _step_notify(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    recipient = str(cfg.get("user") or cfg.get("assignee") or "").strip()
    if not recipient:
        return "failed", "notification step requires a recipient", {}
    if ctx.get("dry_run"):
        return "passed", f"would notify {recipient} (dry run)", {}
    workflow_store.create_notification(
        recipient, cfg.get("text", "Flow finished"), cfg.get("detail", ""),
    )
    return "passed", f"notified {recipient}", {}


def _step_summarize(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_components import KnowledgeDocument
    from mari_components.knowledge import summarize_digest
    rows = workflow_store.documents(ctx.get("doc_ids", []))
    if not rows:
        return "skipped", "no documents to summarize", {"summary": ""}
    result = summarize_digest(
        [KnowledgeDocument(
            str(row["id"]), row["title"], row.get("body") or row.get("snippet") or "",
            revision=str(row.get("updated_src") or ""),
        ) for row in rows],
        generate_json=lambda prompt, _version: llm.generate_json(
            prompt, system="You summarize document sets."),
        maximum_documents=len(rows),
    )
    text = result.summary
    return "passed", text[:160], {"summary": text}


def _step_trigger(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    note = (ctx.get("trigger") or {}).get("note", "")
    return "passed", note or cfg.get("label", "manual run"), {}


def _step_sync_source(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Run the real diff-based ingest sync for one source, synchronously, and
    report honest per-step stats from the sync result."""
    from mari_server.sources import sync as ingest  # late import — ingest imports flowengine at module load
    source_id = int(cfg.get("source_id") or 0)
    name = workflow_store.source_name(source_id)
    if not name:
        return "failed", f"source #{source_id} not found", {}
    if ctx.get("dry_run"):
        return "passed", f"would sync {name} (dry run)", {}
    stats = ingest.run_sync(source_id)
    if stats is None:
        return "skipped", f"{name}: a sync is already running", {}
    if stats.get("error"):
        return "failed", f"{name}: {stats['error']}"[:140], {}
    detail = (f"{name}: {stats['files_changed']} files · {stats['items_changed']} items changed · "
              f"{stats['embedded']} chunks embedded · {stats['skipped']} unchanged")
    return "passed", detail, {"files_changed": stats["files_changed"],
                              "items_changed": stats["items_changed"], "embedded": stats["embedded"]}


def _scan_detail(added: int, scanned: int, note: str, noun: str) -> str:
    detail = (f"{added} new {noun}{'' if added == 1 else 's'} captured "
              f"from {scanned} document{'' if scanned == 1 else 's'}")
    return f"{detail} · {note}" if note else detail


def _step_scan_facts(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Mine the documents this run selected for checkable claims.

    `ctx["doc_ids"]` is the output of the flow's fetch_docs step, and passing it
    through is the whole point of FACT-4: this step used to call a mutation that
    re-ran its own `SELECT … LIMIT 8`, so the step labelled "Read recent
    documents" changed nothing and editing `k` in the flow editor changed
    nothing. A flow whose pipeline has no fetch_docs step still works — the scan
    picks its own batch, as it does from the Facts page."""
    if ctx.get("dry_run"):
        return "passed", "would mine recent documents for claims (dry run)", {}
    from mari_server.knowledge.service import scan_facts_for
    doc_ids = ctx.get("doc_ids") or None
    added, scanned, note = scan_facts_for(doc_ids)
    return "passed", _scan_detail(added, scanned, note, "claim"), {"facts": added}


def _step_scan_decisions(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Mine the documents this run selected for decisions the team made — same
    shape as the fact scan, including reading ctx["doc_ids"] (FACT-4)."""
    if ctx.get("dry_run"):
        return "passed", "would mine recent documents for decisions (dry run)", {}
    from mari_server.knowledge.service import scan_decisions_for
    doc_ids = ctx.get("doc_ids") or None
    added, scanned, note = scan_decisions_for(doc_ids)
    return "passed", _scan_detail(added, scanned, note, "decision"), {"decisions": added}


def _step_refresh_digest(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Regenerate the weekly digest — same logic as the regenerateDigest mutation."""
    if ctx.get("dry_run"):
        return "passed", "would regenerate the weekly digest (dry run)", {}
    from mari_server.knowledge.service import regenerate_digest
    ok = regenerate_digest()
    n = workflow_store.digest_topic_count()
    if not ok:
        return "failed", "LLM unavailable — previous digest kept", {}
    return "passed", f"digest regenerated · {n} topics", {"digest_topics": n}


STEP_IMPLS: dict[str, t.Callable] = {
    "trigger": _step_trigger,
    "fetch_docs": _step_fetch,
    "refine": _step_refine,
    "fact_check": _step_fact_check,
    "condition": _step_condition,
    "tag": _step_tag,
    "derive_links": _step_derive_links,
    "create_task": _step_create_task,
    "approval": _step_approval,
    "notify": _step_notify,
    "summarize": _step_summarize,
    "sync_source": _step_sync_source,
    "refresh_digest": _step_refresh_digest,
    "scan_facts": _step_scan_facts,
    "scan_decisions": _step_scan_decisions,
}

# steps that call the local LLM (shown as slow in the UI)
LLM_STEPS = {"refine", "fact_check", "derive_links", "summarize", "refresh_digest", "scan_facts", "scan_decisions"}

# Steps that are safe to run a second time after a transient failure (FLOW-3).
# A failed step used to end the run outright, with no retry and no way to tell a
# dropped connection from a real error — so a flow that had already tagged and
# notified would abandon its remaining work because one query timed out.
#
# Membership is a claim about the step, not a preference: every step here either
# reads only, or writes through ON CONFLICT DO NOTHING / an idempotent upsert, so
# running it twice lands the database where running it once would have.
#
# Deliberately absent: `approval` (pauses rather than fails) and `condition`
# (cannot fail transiently).
RETRYABLE_STEPS = {"fetch_docs", "tag", "create_task", "notify", "summarize",
                   "derive_links", "sync_source", "scan_facts", "scan_decisions",
                   "fact_check", "refine", "refresh_digest"}
STEP_RETRIES = 1        # one extra attempt, not a loop
STEP_RETRY_BACKOFF = 2.0  # seconds


def _run_step(kind: str, impl: t.Callable | None, cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Run one step, retrying an idempotent one once on an exception.

    The retry is recorded in the step's own detail rather than hidden: a run
    that needed two attempts and a run that needed one are different facts about
    the system, and the history is the only place anyone can see the difference."""
    return run_step(
        kind, impl, cfg, ctx, retryable=RETRYABLE_STEPS,
        retries=STEP_RETRIES, backoff=STEP_RETRY_BACKOFF,
    )


def _persist(run_id: int, rows: list[dict], status: str, progress: int, stats: dict, start: float) -> None:
    workflow_store.save_run_progress(
        run_id, rows=rows, status=status, progress=progress,
        stats=stats, duration=_elapsed(start),
    )


def execute_run(run_id: int, resume_from: int = 0) -> None:
    """Run (or resume) a workflow's steps sequentially, persisting after each."""
    loaded = workflow_store.load_run(run_id)
    if not loaded:
        return
    run, wf = loaded
    steps = wf["nodes"] if isinstance(wf["nodes"], list) else json.loads(wf["nodes"] or "[]")
    rows = run["rows_data"] if isinstance(run["rows_data"], list) else json.loads(run["rows_data"] or "[]")
    ctx: dict = (run["stats"] if isinstance(run["stats"], dict) else json.loads(run["stats"] or "{}")).get("ctx", {})
    start = time.time()

    if not rows:
        rows = [{"step": s.get("label", s.get("kind", "step")), "status": "pending", "detail": ""} for s in steps]

    for i in range(resume_from, len(steps)):
        step = steps[i]
        kind = step.get("kind", "trigger")
        rows[i]["status"] = "running"
        _persist(run_id, rows, "running", int(i / max(len(steps), 1) * 100), {"ctx": ctx}, start)
        t0 = time.time()
        status, detail, updates = _run_step(kind, STEP_IMPLS.get(kind), step.get("config", {}), ctx)
        ctx.update({k: v for k, v in updates.items() if k != "pause"})
        rows[i].update({"status": status, "detail": detail, "duration": _elapsed(t0)})
        if status == "waiting":
            _persist(run_id, rows, "waiting", int((i + 1) / len(steps) * 100),
                     {"ctx": ctx, "paused_at": i, **_public_stats(ctx)}, start)
            return
        if status == "failed":
            _persist(run_id, rows, "failed", 100, {"ctx": ctx, **_public_stats(ctx)}, start)
            return
    _persist(run_id, rows, "passed", 100, {"ctx": ctx, **_public_stats(ctx)}, start)


def _public_stats(ctx: dict) -> dict:
    # only real, per-run numbers — the UI renders a stat tile per key present,
    # so fields with no honest value are simply omitted
    stats = {"contradictions": ctx.get("contradictions", 0),
             "edits": ctx.get("edits", 0), "links": ctx.get("links", 0)}
    # Only runs that actually scanned report a count; a zero here would read as
    # "the scan found nothing" on every run that never scanned.
    if "facts" in ctx:
        stats["facts"] = ctx["facts"]
    if "decisions" in ctx:
        stats["decisions"] = ctx["decisions"]
    return stats


# ————— the run worker pool (FLOW-4) —————
#
# `start_run` used to spawn a bare, uncapped thread per run, each opening its own
# Postgres connection outside db.py's pool. Nothing bounded that: a schedule that
# fires faster than its runs finish, or a document-trigger batch that matches
# many workflows, opened as many connections as there were runs — while the
# request path shared a pool of ten. Postgres refuses connections long before
# Python refuses threads, so the failure landed on whoever was using the console.
#
# A bounded pool makes the ceiling explicit and moves the queue into this process
# where it is visible, instead of into the database's connection limit. Runs past
# the ceiling wait their turn rather than being dropped; an approval step frees
# its worker immediately, because a waiting run returns from execute_run and is
# resumed later by approveRun.
FLOW_WORKERS = max(1, int(config.get("runtime", "flow_workers", 4)))
_run_pool = cf.ThreadPoolExecutor(max_workers=FLOW_WORKERS, thread_name_prefix="mari-flow")


def _guarded_run(run_id: int, resume_from: int, project_access: access.AccessContext) -> None:
    """execute_run, with a last-resort failure record. A run whose execution
    raised before it could persist anything would otherwise sit at 'running'
    until the next restart reconciled it — a run that says it is still going
    when nothing is going is the worst of the three possible states."""
    try:
        with access.use_access(project_access):
            execute_run(run_id, resume_from)
    except Exception as e:  # noqa: BLE001
        try:
            workflow_store.fail_running_run(
                run_id, f"run failed to start: {type(e).__name__}: {e}",
            )
        except Exception:  # noqa: BLE001
            pass


def start_run(run_id: int, resume_from: int = 0) -> None:
    """Queue a run on the worker pool. Returns immediately; the caller gets the
    run id and follows it through workflowRun."""
    project_access = access.current_access()
    if project_access is None:
        project = workflow_store.run_project(run_id)
        if not project:
            workflow_store.fail_unroutable_run(run_id, "run has no active project")
            return
        project_access = access.external_access(
            int(project["id"]), str(project["slug"]), str(project["name"]),
            "automation", str(run_id),
            frozenset({"knowledge.read", "knowledge.write", "automation.run", "source.sync"}),
        )
    _run_pool.submit(_guarded_run, run_id, resume_from, project_access)


# ————— document triggers (init.sql: workflows.trigger jsonb) —————
#
# Trigger shape: {"on": "document_changed"|"document_added"|"", "source_id": int|null,
#                 "tag": str|null, "path_glob": str|null}. Empty "on" = manual-only.


def _trigger_matches(trig: dict, change: str, docs: list[dict], doc_tags: dict[int, set]) -> list[dict]:
    """Return the subset of changed docs this trigger matches (ANDed filters)."""
    return list(matching_documents(trig, change, docs, doc_tags))


def fire_document_triggers(doc_ids: list[int], change: str) -> list[int]:
    """Start real runs for every active workflow whose trigger matches this
    change batch. Each workflow fires at most once per batch (debounce), with
    the matching docs injected as ctx.trigger_doc_ids so _step_fetch operates
    on them. Returns the started run ids."""
    if not doc_ids or change not in ("document_changed", "document_added"):
        return []
    docs, workflows, doc_tags = workflow_store.trigger_inputs(doc_ids, change)
    if not docs or not workflows:
        return []
    run_ids: list[int] = []
    verb = "updated" if change == "document_changed" else "added"
    for workflow in workflows:
        trigger = (workflow["trigger"] if isinstance(workflow["trigger"], dict)
                   else json.loads(workflow["trigger"] or "{}"))
        matched = _trigger_matches(trigger, change, docs, doc_tags)
        if not matched:
            continue
        first = matched[0].get("source_path") or matched[0]["title"]
        note = f"Triggered by: {first} {verb}" + (
            f" (+{len(matched) - 1} more)" if len(matched) > 1 else "")
        trigger_meta = {"on": change, "doc_ids": [doc["id"] for doc in matched], "note": note}
        run_ids.append(workflow_store.create_triggered_run(
            workflow, trigger_meta["doc_ids"], trigger_meta, note,
        ))
    for run_id in run_ids:
        start_run(run_id)
    return run_ids


# ————— schedule triggers (init.sql: workflow_runs.started_at) —————
#
# Trigger shape: {"on": "schedule", "every_minutes": N} (1..10080). A daemon
# thread scans every 30s and starts runs that are due; "last started" is
# derived from the latest run's started_at — no per-workflow state to keep.

SCHEDULER_TICK_SECONDS = 30
_SCHEDULER = {"started": False, "thread": None, "stop": threading.Event()}
PROCESS_START_TS = time.time()  # for startup reconciliation — never touch newer rows


def reconcile_stale_runs() -> int:
    """Startup reconciliation: a run left 'running' by an unclean shutdown has
    no process behind it and blocks run_due_schedules forever (it sees the
    latest run as still in progress). Mark those failed; runs this process
    started are never touched. Returns how many were flipped.

    'waiting' is NOT reconciled (FLOW-1). A waiting run is paused at an approval
    step, by design, indefinitely — that is what an approval is. Sweeping it up
    with the crashed runs meant every restart destroyed every pending approval
    and told the person waiting on it that their sign-off had been "interrupted
    by restart", which was not true: nothing was interrupted, the server was
    simply started again. A waiting run needs no process to be alive; approveRun
    resumes it from `paused_at` whenever someone gets to it."""
    return workflow_store.reconcile_stale_runs(PROCESS_START_TS)


def run_due_schedules() -> list[int]:
    """One scheduler pass: start a run for every active schedule-triggered
    workflow that is due (now - last started >= every_minutes). A workflow
    whose latest run is still running/waiting is never double-started.
    Returns the started run ids."""
    started: list[int] = []
    for workflow in workflow_store.scheduled_workflows():
        trigger = (workflow["trigger"] if isinstance(workflow["trigger"], dict)
                   else json.loads(workflow["trigger"] or "{}"))
        try:
            every = int(trigger.get("every_minutes") or 0)
        except (TypeError, ValueError):
            continue
        if every < 1:
            continue
        last = workflow_store.latest_run(workflow["id"], every)
        if last and last["status"] in ("running", "waiting"):
            continue
        if last and not last["due"]:
            continue
        label = f"Scheduled · every {every} min"
        trigger_meta = {"on": "schedule", "every_minutes": every, "note": label}
        started.append(workflow_store.create_scheduled_run(
            workflow, trigger_meta, label,
        ))
    for rid in started:
        start_run(rid)
    return started


def start_scheduler() -> None:
    """Start the schedule-trigger daemon thread (guarded against uvicorn
    --reload / repeat imports double-starting it)."""
    if _SCHEDULER["started"]:
        return
    _SCHEDULER["started"] = True
    _SCHEDULER["stop"].clear()

    # Reconcile BEFORE the loop begins: runs wedged in 'running'/'waiting' by
    # an unclean shutdown would otherwise block their schedule forever.
    try:
        reconcile_stale_runs()
    except Exception:  # noqa: BLE001 — reconciliation must never block startup
        pass

    def loop() -> None:
        while not _SCHEDULER["stop"].wait(SCHEDULER_TICK_SECONDS):
            try:
                run_due_schedules()
            except Exception:  # noqa: BLE001 — the scheduler must survive transient DB errors
                pass

    thread = threading.Thread(target=loop, daemon=True, name="flow-scheduler")
    _SCHEDULER["thread"] = thread
    thread.start()


def stop_scheduler(timeout: float = 5.0) -> None:
    """Stop the scheduler loop during an orderly ASGI shutdown."""
    if not _SCHEDULER["started"]:
        return
    _SCHEDULER["stop"].set()
    thread = _SCHEDULER.get("thread")
    if thread and thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))
    _SCHEDULER.update(started=False, thread=None)


# ————— seeded scheduled flows (startup + connectGithubRepo) —————


def _wf_nodes(row: dict) -> list[dict]:
    return row["nodes"] if isinstance(row["nodes"], list) else json.loads(row["nodes"] or "[]")


def ensure_sync_flow(source_id: int, repo: str) -> int | None:
    """Idempotently create the scheduled 'Sync <label>' flow for a github or
    connector source. Returns the new workflow id, or None if one exists."""
    existing = workflow_store.find_by_step("sync_source", project_scoped=False)
    if existing and any(
        int((step.get("config") or {}).get("source_id") or 0) == source_id
        for step in existing["nodes"] if isinstance(step, dict)
    ):
        return None
    nodes = [
        {"kind": "trigger", "label": "Every 10 min", "config": {"label": "Scheduled · every 10 min"}},
        {"kind": "sync_source", "label": f"Sync {repo}", "config": {"source_id": source_id}},
    ]
    return workflow_store.create_default_workflow(
        name=f"Sync {repo}"[:120],
        description=f"Keeps {repo} indexed — incremental sync on a schedule.",
        color="#5c7a4c", status="active", nodes=nodes,
        trigger={"on": "schedule", "every_minutes": 10}, project_scoped=False,
    )


def ensure_digest_flow() -> int | None:
    """Idempotently create the 'Weekly digest refresh' flow (schedule: every
    10080 min == weekly). The old settings.digest_schedule.enabled decides
    whether it starts active or paused. Returns the new id or None."""
    if workflow_store.find_by_step("refresh_digest", project_scoped=False):
        return None
    status = "active" if workflow_store.setting("digest_schedule").get("enabled", True) else "paused"
    nodes = [
        {"kind": "trigger", "label": "Every week", "config": {"label": "Scheduled · weekly"}},
        {"kind": "refresh_digest", "label": "Refresh weekly digest", "config": {}},
    ]
    return workflow_store.create_default_workflow(
        name="Weekly digest refresh",
        description="Regenerates the Overview digest from recent documents and facts.",
        color="#c8973a", status=status, nodes=nodes,
        trigger={"on": "schedule", "every_minutes": 10080}, project_scoped=False,
    )


FACT_SCAN_FLOW = "Hourly fact extraction"
DECISION_SCAN_FLOW = "Decision scan"


def _adopt_rotation(row: dict, scan_kind: str, rotate: str) -> None:
    """Teach an already-seeded scan flow to rotate its document selection.

    A workspace that ran an earlier version has the flow with `{"k": 8}` and no
    `rotate`, so its fetch_docs step still hands the scan the newest eight
    documents every time — the flow half of FACT-2 would stay broken on upgrade
    while a fresh install got the fix.

    This only touches a pipeline that is still exactly as it shipped: three
    steps, the expected kinds in the expected order, and a fetch_docs step that
    has never been given a `rotate`. Anything a person has edited is left alone.
    A pipeline is the user's; a default is ours."""
    nodes = _wf_nodes(row)
    kinds = [s.get("kind") for s in nodes if isinstance(s, dict)]
    if kinds != ["trigger", "fetch_docs", scan_kind]:
        return
    cfg = nodes[1].get("config") or {}
    changed = False
    if "rotate" not in cfg:
        cfg = {**cfg, "rotate": rotate}
        changed = True
    if scan_kind == "scan_facts" and int(cfg.get("k") or 8) == 8:
        cfg = {**cfg, "k": 50}
        nodes[1]["label"] = "Read new and changed documents"
        changed = True
    if not changed:
        return
    nodes[1]["config"] = cfg
    workflow_store.update_nodes(row["id"], nodes)


def ensure_fact_scan_flow() -> int:
    """Get or create the hourly fact extraction flow for this project."""
    existing = workflow_store.find_by_step("scan_facts")
    if existing:
        _adopt_rotation(existing, "scan_facts", "facts")
        workflow_store.activate_hourly_fact_scan(existing["id"])
        return existing["id"]
    nodes = [
            {"kind": "trigger", "label": "Every hour", "config": {"label": "Scheduled · hourly"}},
            {"kind": "fetch_docs", "label": "Read new and changed documents",
             "config": {"k": 50, "rotate": "facts"}},
            {"kind": "scan_facts", "label": "Extract checkable claims", "config": {}},
        ]
    return workflow_store.create_default_workflow(
        name=FACT_SCAN_FLOW,
        description="Scans new and changed documents for atomic, checkable claims every hour.",
        color="#1E6FA8", status="active", nodes=nodes,
        trigger={"on": "schedule", "every_minutes": 60},
    )


def ensure_decision_scan_flow() -> int:
    """Get-or-create the manual 'Decision scan' flow the Decisions page starts.
    Mirrors ensure_fact_scan_flow — same shape, different step."""
    existing = workflow_store.find_by_step("scan_decisions")
    if existing:
        _adopt_rotation(existing, "scan_decisions", "decisions")
        return existing["id"]
    nodes = [
            {"kind": "trigger", "label": "Manual", "config": {"label": "Started from Decisions"}},
            {"kind": "fetch_docs", "label": "Read documents (least recently scanned)",
             "config": {"k": 8, "rotate": "decisions"}},
            {"kind": "scan_decisions", "label": "Extract decisions", "config": {}},
        ]
    return workflow_store.create_default_workflow(
        name=DECISION_SCAN_FLOW,
        description="Mines recent documents for decisions the team made and files them awaiting sign-off.",
        color="#7A2E1F", status="active", nodes=nodes, trigger={"on": ""},
    )


def seed_scheduled_flows() -> None:
    """Startup seeding: every github/connector source gets a scheduled sync
    flow; the weekly digest gets a refresh flow. Idempotent — existing kept."""
    for s in workflow_store.connector_sources():
        cfg = s["config"] if isinstance(s["config"], dict) else json.loads(s["config"] or "{}")
        ensure_sync_flow(s["id"], cfg.get("repo") or s["display_name"])
    ensure_digest_flow()
    for project in workflow_store.active_projects():
        project_access = access.external_access(
            int(project["id"]), str(project["slug"]), str(project["name"]),
            "automation", "fact-extraction-seed",
            frozenset({"knowledge.read", "knowledge.write", "automation.run"}),
        )
        with access.use_access(project_access):
            ensure_fact_scan_flow()

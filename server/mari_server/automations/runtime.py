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
from mari_server.automations import progress
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
        trigger_ids=trigger_ids, tag=tag, query=query, limit=min(k, 200), rotation=rotation,
        source_ids=cfg.get("source_ids") or [],
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


# Still a live workflow step even though the Review PAGE is gone: a scheduled
# or promoted workflow can still file a review task, and the review projection
# (persistence/postgres/review.py) still reads those rows for policy
# auto-approval and inline approvals. Do not remove with the Review UI.
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
    from mari_server.knowledge.service import extract_fact_candidates_for
    from mari_server.persistence.postgres import knowledge as knowledge_store
    doc_ids = ctx.get("doc_ids") or None
    candidates, scanned, note = extract_fact_candidates_for(
        doc_ids,
        claims_per_document=max(1, min(int(cfg.get("claims_per_document") or 2), 10)),
        instructions=str(cfg.get("instructions") or ""),
        run_id=int(ctx["run_id"]),
        max_llm_calls=max(0, min(int(cfg.get("max_llm_calls") or 50), 200)),
        max_input_tokens=max(0, min(int(cfg.get("max_input_tokens") or 100000), 2_000_000)),
        max_output_tokens=max(0, min(int(cfg.get("max_output_tokens") or 20000), 400_000)),
    )
    added = knowledge_store.stage_fact_candidates(int(ctx["run_id"]), candidates)
    return "passed", _scan_detail(added, scanned, note, "claim"), {"facts": added}


def _step_review_facts(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.knowledge.service import apply_ai_fact_proposals
    from mari_server.persistence.postgres import knowledge as knowledge_store
    run_id = int(ctx["run_id"])
    mode = str(cfg.get("mode") or "human")
    if ctx.get("dry_run"):
        return "passed", f"would use {mode} review (dry run)", {}
    if mode == "ai":
        counts = apply_ai_fact_proposals(
            run_id, minimum_confidence=float(cfg.get("minimum_confidence") or .8),
        )
        if counts["pending"]:
            return "waiting", (
                f"AI applied bounded proposals; {counts['pending']} candidates need human review"
            ), {"pause": True, "accepted_facts": counts["accepted"],
                "rejected_facts": counts["rejected"]}
        detail = f"AI accepted {counts['accepted']} · rejected {counts['rejected']}"
        return "passed", detail, {"accepted_facts": counts["accepted"], "rejected_facts": counts["rejected"]}
    counts = knowledge_store.fact_candidate_counts(run_id)
    if counts["pending"]:
        return "waiting", f"{counts['pending']} candidates awaiting human review", {"pause": True}
    detail = f"Human accepted {counts['accepted']} · rejected {counts['rejected']}"
    return "passed", detail, {"accepted_facts": counts["accepted"], "rejected_facts": counts["rejected"]}


def _step_map_fact_impact(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.knowledge.service import map_fact_candidate_impact
    if ctx.get("dry_run"):
        return "passed", "would map related facts and temporal evidence (dry run)", {}
    stats = map_fact_candidate_impact(
        int(ctx["run_id"]),
        retrieval_backend=str(cfg.get("retrieval_backend") or "postgres"),
        fact_neighbors=max(1, min(int(cfg.get("fact_neighbors") or 8), 50)),
        evidence_neighbors=max(1, min(int(cfg.get("evidence_neighbors") or 8), 50)),
        minimum_fact_similarity=float(cfg.get("minimum_fact_similarity") or .72),
        minimum_evidence_similarity=float(cfg.get("minimum_evidence_similarity") or .68),
        max_components=max(1, min(int(cfg.get("max_components") or 12), 32)),
    )
    detail = (f"{stats.get('embedded_components', 0)} component vectors · "
              f"{stats['impact_links']} evidence links · "
              f"{stats['high_impact_facts']} high-impact candidates")
    return "passed", detail, stats


def _step_adjudicate_facts(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.knowledge.service import adjudicate_fact_candidates
    if ctx.get("dry_run"):
        mode = str(cfg.get("mode") or "off")
        return "passed", f"would use {mode} temporal evidence adjudication (dry run)", {}
    stats = adjudicate_fact_candidates(
        int(ctx["run_id"]), enabled=str(cfg.get("mode") or "off") == "llm",
        max_calls=max(0, min(int(cfg.get("max_calls") or 10), 100)),
        max_input_tokens=max(0, min(int(cfg.get("max_input_tokens") or 24000), 1_000_000)),
        max_output_tokens=max(0, min(int(cfg.get("max_output_tokens") or 8000), 200_000)),
        output_tokens_per_call=max(100, min(int(cfg.get("output_tokens_per_call") or 800), 4000)),
        related_assertions=max(1, min(int(cfg.get("related_assertions") or 8), 20)),
        evidence_spans=max(1, min(int(cfg.get("evidence_spans") or 12), 30)),
        instructions=str(cfg.get("instructions") or ""),
    )
    if str(cfg.get("mode") or "off") != "llm":
        return "passed", "LLM adjudication disabled · human review retains embedding context", stats
    detail = (f"{stats['adjudicated_facts']} candidates adjudicated · "
              f"{stats['llm_calls']} bounded LLM calls · "
              f"{stats['llm_abstentions']} abstentions")
    if stats["llm_budget_exhausted"]:
        detail += " · budget exhausted; remaining candidates require human review"
    return "passed", detail, stats


def _step_cluster_facts(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.knowledge.service import build_fact_clusters
    if ctx.get("dry_run"):
        mode = str(cfg.get("label_mode") or "off")
        return "passed", f"would build embedding clusters with {mode} labels (dry run)", {}
    stats = build_fact_clusters(
        int(ctx["run_id"]),
        minimum_similarity=float(cfg.get("minimum_similarity") or .78),
        label_mode=str(cfg.get("label_mode") or "off"),
        max_llm_clusters=max(0, min(int(cfg.get("max_llm_clusters") or 5), 50)),
        max_input_tokens=max(0, min(int(cfg.get("max_input_tokens") or 8000), 500_000)),
        max_output_tokens=max(0, min(int(cfg.get("max_output_tokens") or 2000), 100_000)),
        instructions=str(cfg.get("instructions") or ""),
    )
    detail = (f"{stats['fact_clusters']} embedding clusters · "
              f"{stats['cluster_llm_calls']} visible label calls")
    if stats["cluster_llm_budget_exhausted"]:
        detail += " · label budget exhausted"
    return "passed", detail, stats


def _step_publish_facts(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from mari_server.identity.actor import actor_name
    from mari_server.persistence.postgres import knowledge as knowledge_store
    if ctx.get("dry_run"):
        return "passed", "would publish accepted candidates (dry run)", {}
    verified = str(cfg.get("status") or "needs_review") == "verified"
    published = knowledge_store.publish_fact_candidates(
        int(ctx["run_id"]), actor_name(), verified=verified,
    )
    destination = "Verified" if verified else "Needs review"
    return "passed", f"{published} accepted claims published as {destination}", {"published_facts": published}


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
    "map_fact_impact": _step_map_fact_impact,
    "adjudicate_facts": _step_adjudicate_facts,
    "cluster_facts": _step_cluster_facts,
    "review_facts": _step_review_facts,
    "publish_facts": _step_publish_facts,
    "scan_decisions": _step_scan_decisions,
}

# steps that call the local LLM (shown as slow in the UI)
LLM_STEPS = {"refine", "fact_check", "derive_links", "summarize", "refresh_digest", "scan_facts", "adjudicate_facts", "cluster_facts", "review_facts", "scan_decisions"}

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
                   "derive_links", "sync_source", "scan_facts", "map_fact_impact", "adjudicate_facts", "cluster_facts", "review_facts", "publish_facts", "scan_decisions",
                   "fact_check", "refine", "refresh_digest"}
STEP_RETRIES = 1        # one extra attempt, not a loop
STEP_RETRY_BACKOFF = 2.0  # seconds


def _step_reporter(run_id: int, rows: list[dict], ctx: dict, start: float,
                   step_index: int, step_count: int):
    """A reporter that maps one step's (done, total) into the run's bar.

    Persists only when the whole-run percentage changes, so a 50-document
    scan writes tens of heartbeats, not thousands."""
    last = {"pct": -1}

    def report(done: int, total: int) -> None:
        share = min(max(done, 0), total) / max(total, 1)
        pct = int((step_index + share) / max(step_count, 1) * 100)
        if pct <= last["pct"]:
            return
        last["pct"] = pct
        _persist(run_id, rows, "running", pct, {"ctx": ctx}, start)

    return report


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
    ctx.setdefault("run_id", run_id)
    start = time.time()

    templates = [{"step": s.get("label", s.get("kind", "step")),
                  "status": "pending", "detail": ""} for s in steps]
    if not rows:
        rows = templates
    elif len(rows) != len(steps) or any(
            row.get("step") != template["step"] for row, template in zip(rows, templates)):
        # Workflow defaults can gain a stage while an approval run is waiting.
        # Align persisted rows by their stable display label so resume has one
        # row per current step; unmatched new stages start pending, while the
        # old completed/review rows keep their evidence and duration.
        unused = list(rows)
        aligned: list[dict] = []
        for template in templates:
            match = next((row for row in unused if row.get("step") == template["step"]), None)
            if match is None:
                aligned.append(template)
            else:
                aligned.append(match)
                unused.remove(match)
        rows = aligned

    for i in range(resume_from, len(steps)):
        step = steps[i]
        kind = step.get("kind", "trigger")
        rows[i]["status"] = "running"
        _persist(run_id, rows, "running", int(i / max(len(steps), 1) * 100), {"ctx": ctx}, start)
        t0 = time.time()
        token = progress.arm(_step_reporter(run_id, rows, ctx, start, i, len(steps)))
        try:
            status, detail, updates = _run_step(kind, STEP_IMPLS.get(kind), step.get("config", {}), ctx)
        finally:
            progress.disarm(token)
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
    for key in ("accepted_facts", "rejected_facts", "published_facts",
                "impact_links", "high_impact_facts"):
        if key in ctx:
            stats[key] = ctx[key]
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
    existing = workflow_store.find_by_step(
        "sync_source", config={"source_id": source_id},
    )
    if existing:
        return None
    nodes = [
        {"kind": "trigger", "label": "Every 10 min", "config": {"label": "Scheduled · every 10 min"}},
        {"kind": "sync_source", "label": f"Sync {repo}", "config": {"source_id": source_id}},
    ]
    return workflow_store.create_default_workflow(
        name=f"Sync {repo}"[:120],
        description=f"Keeps {repo} indexed — incremental sync on a schedule.",
        color="#5c7a4c", status="active", nodes=nodes,
        trigger={"on": "schedule", "every_minutes": 10},
    )


def ensure_digest_flow() -> int | None:
    """Idempotently create the 'Weekly digest refresh' flow (schedule: every
    10080 min == weekly). The old settings.digest_schedule.enabled decides
    whether it starts active or paused. Returns the new id or None."""
    if workflow_store.find_by_step("refresh_digest"):
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
        trigger={"on": "schedule", "every_minutes": 10080},
    )


FACT_SCAN_FLOW = "Fact extraction"
LEGACY_FACT_SCAN_FLOW = "Hourly fact extraction"
FACT_SCAN_DESCRIPTION = (
    "Scans new and changed documents for atomic, checkable claims on the configured schedule."
)
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


def _adopt_fact_review(workflow_id: int) -> None:
    """Upgrade the shipped three-step scanner to its staged review workflow.

    Only the known legacy shape is changed. A custom pipeline remains the
    owner's pipeline even when it happens to contain a scan_facts step.
    """
    nodes = workflow_store.workflow_nodes(workflow_id)
    if [node.get("kind") for node in nodes] != ["trigger", "fetch_docs", "scan_facts"]:
        return
    nodes.extend([
        {"kind": "review_facts", "label": "Review candidates", "config": {"mode": "human"}},
        {"kind": "publish_facts", "label": "Publish accepted facts", "config": {"status": "needs_review"}},
    ])
    workflow_store.update_nodes(workflow_id, nodes)


def _adopt_fact_impact(workflow_id: int) -> None:
    """Insert impact mapping into the shipped staged workflow only."""
    nodes = workflow_store.workflow_nodes(workflow_id)
    if [node.get("kind") for node in nodes] != [
        "trigger", "fetch_docs", "scan_facts", "review_facts", "publish_facts",
    ]:
        return
    nodes.insert(3, {
        "kind": "map_fact_impact", "label": "Map related facts and evidence", "config": {},
    })
    workflow_store.update_nodes(workflow_id, nodes)


def _adopt_fact_intelligence(workflow_id: int) -> None:
    """Add bounded adjudication and clustering to the shipped six-stage flow."""
    nodes = workflow_store.workflow_nodes(workflow_id)
    if [node.get("kind") for node in nodes] != [
        "trigger", "fetch_docs", "scan_facts", "map_fact_impact",
        "review_facts", "publish_facts",
    ]:
        return
    nodes[3]["label"] = "Embed facts and retrieve evidence"
    nodes[3]["config"] = {
        "retrieval_backend": "postgres", "fact_neighbors": 8,
        "evidence_neighbors": 8, "minimum_fact_similarity": .72,
        "minimum_evidence_similarity": .68, "max_components": 12,
    }
    nodes.insert(4, {
        "kind": "adjudicate_facts", "label": "Judge candidate quality and evidence",
        "config": {"mode": "llm", "max_calls": 50, "max_input_tokens": 120000,
                   "max_output_tokens": 40000, "output_tokens_per_call": 800,
                   "related_assertions": 8, "evidence_spans": 12},
    })
    nodes.insert(5, {
        "kind": "cluster_facts", "label": "Build fact clusters",
        "config": {"label_mode": "off", "minimum_similarity": .78,
                   "max_llm_clusters": 5, "max_input_tokens": 8000,
                   "max_output_tokens": 2000},
    })
    nodes[6]["label"] = "Apply judge verdicts; review uncertainty"
    nodes[6]["config"] = {"mode": "ai", "minimum_confidence": .85}
    workflow_store.update_nodes(workflow_id, nodes)


def _normalize_fact_intelligence_config(workflow_id: int) -> None:
    """Fill newly shipped bounds without overwriting user-owned values."""
    nodes = workflow_store.workflow_nodes(workflow_id)
    if [node.get("kind") for node in nodes] != [
        "trigger", "fetch_docs", "scan_facts", "map_fact_impact",
        "adjudicate_facts", "cluster_facts", "review_facts", "publish_facts",
    ]:
        return
    defaults = {
        "scan_facts": {"claims_per_document": 2, "max_llm_calls": 50,
                       "max_input_tokens": 100000, "max_output_tokens": 20000},
        "map_fact_impact": {"retrieval_backend": "postgres", "fact_neighbors": 8,
                            "evidence_neighbors": 8, "minimum_fact_similarity": .72,
                            "minimum_evidence_similarity": .68, "max_components": 12},
        "adjudicate_facts": {"mode": "llm", "max_calls": 50,
                             "max_input_tokens": 120000, "max_output_tokens": 40000,
                             "output_tokens_per_call": 800, "related_assertions": 8,
                             "evidence_spans": 12},
        "cluster_facts": {"label_mode": "off", "minimum_similarity": .78,
                          "max_llm_clusters": 5, "max_input_tokens": 8000,
                          "max_output_tokens": 2000},
    }
    changed = False
    for node in nodes:
        if node.get("kind") not in defaults:
            continue
        config = dict(node.get("config") or {})
        merged = {**defaults[node["kind"]], **config}
        if merged != config:
            node["config"] = merged
            changed = True
    if changed:
        workflow_store.update_nodes(workflow_id, nodes)


def ensure_fact_scan_flow() -> int:
    """Get or create the scheduled fact extraction flow for this project."""
    existing = workflow_store.find_by_step("scan_facts")
    if existing:
        if (existing.get("name") == LEGACY_FACT_SCAN_FLOW and
                existing.get("description") ==
                "Scans new and changed documents for atomic, checkable claims every hour."):
            workflow_store.update_metadata(
                existing["id"], FACT_SCAN_FLOW, FACT_SCAN_DESCRIPTION,
            )
        _adopt_rotation(existing, "scan_facts", "facts")
        _adopt_fact_review(existing["id"])
        _adopt_fact_impact(existing["id"])
        _adopt_fact_intelligence(existing["id"])
        _normalize_fact_intelligence_config(existing["id"])
        return existing["id"]
    nodes = [
            {"kind": "trigger", "label": "Every hour", "config": {"label": "Scheduled · hourly"}},
            {"kind": "fetch_docs", "label": "Read new and changed documents",
             "config": {"k": 50, "rotate": "facts"}},
            {"kind": "scan_facts", "label": "Extract checkable claims", "config": {
                "max_llm_calls": 50, "max_input_tokens": 100000,
                "max_output_tokens": 20000,
            }},
            {"kind": "map_fact_impact", "label": "Embed facts and retrieve evidence", "config": {
                "retrieval_backend": "postgres", "fact_neighbors": 8,
                "evidence_neighbors": 8, "minimum_fact_similarity": .72,
                "minimum_evidence_similarity": .68, "max_components": 12,
            }},
            {"kind": "adjudicate_facts", "label": "Judge candidate quality and evidence", "config": {
                "mode": "llm", "max_calls": 50, "max_input_tokens": 120000,
                "max_output_tokens": 40000, "output_tokens_per_call": 800,
                "related_assertions": 8, "evidence_spans": 12,
            }},
            {"kind": "cluster_facts", "label": "Build fact clusters", "config": {
                "label_mode": "off", "minimum_similarity": .78,
                "max_llm_clusters": 5, "max_input_tokens": 8000,
                "max_output_tokens": 2000,
            }},
            {"kind": "review_facts", "label": "Apply judge verdicts; review uncertainty", "config": {
                "mode": "ai", "minimum_confidence": .85,
            }},
            {"kind": "publish_facts", "label": "Publish accepted facts", "config": {"status": "needs_review"}},
        ]
    return workflow_store.create_default_workflow(
        name=FACT_SCAN_FLOW,
        description=FACT_SCAN_DESCRIPTION,
        color="#1E6FA8", status="active", nodes=nodes,
        trigger={"on": "schedule", "every_minutes": 60},
    )


def configure_fact_scan_flow(workflow_id: int, raw: dict | None) -> dict:
    """Validate and persist parameters shared by manual and scheduled scans."""
    raw = raw if isinstance(raw, dict) else {}
    limit = max(1, min(int(raw.get("limit") or 50), 200))
    claims = max(1, min(int(raw.get("claims_per_document") or 2), 10))
    source_ids = sorted({int(value) for value in raw.get("source_ids") or [] if int(value) > 0})
    query = str(raw.get("query") or "").strip()[:200]
    tag = str(raw.get("tag") or "").strip()[:80]
    schedule = int(raw.get("schedule_minutes") or 0)
    review_mode = str(raw.get("review_mode") or "ai")
    if review_mode not in {"human", "ai"}:
        raise ValueError("Fact review mode must be human or AI.")
    instructions = str(raw.get("review_instructions") or "").strip()[:1000]
    publish_status = str(raw.get("publish_status") or "needs_review")
    if publish_status not in {"needs_review", "verified"}:
        raise ValueError("Published facts must need review or be verified.")
    if schedule not in {0, 60, 360, 1440, 10080}:
        raise ValueError("Fact scan schedule must be manual, hourly, every 6 hours, daily, or weekly.")
    retrieval_backend = str(raw.get("retrieval_backend") or "postgres")
    if retrieval_backend != "postgres":
        raise ValueError("Fact retrieval currently uses Postgres vectors.")
    fact_neighbors = max(1, min(int(raw.get("fact_neighbors") or 8), 50))
    evidence_neighbors = max(1, min(int(raw.get("evidence_neighbors") or 8), 50))
    max_components = max(1, min(int(raw.get("max_components") or 12), 32))
    fact_similarity = max(-1.0, min(float(raw.get("minimum_fact_similarity") or .72), 1.0))
    evidence_similarity = max(-1.0, min(float(raw.get("minimum_evidence_similarity") or .68), 1.0))
    extraction_calls = max(0, min(int(raw.get("extraction_max_calls") or limit), 200))
    extraction_input = max(0, min(int(raw.get("extraction_max_input_tokens") or 100000), 2_000_000))
    extraction_output = max(0, min(int(raw.get("extraction_max_output_tokens") or 20000), 400_000))
    adjudication_mode = str(raw.get("adjudication_mode") or "llm")
    if adjudication_mode not in {"off", "llm"}:
        raise ValueError("Fact adjudication must be off or use the configured LLM.")
    adjudication_calls = max(0, min(int(raw.get("adjudication_max_calls") or 50), 100))
    adjudication_input = max(0, min(int(raw.get("adjudication_max_input_tokens") or 120000), 1_000_000))
    adjudication_output = max(0, min(int(raw.get("adjudication_max_output_tokens") or 40000), 200_000))
    cluster_label_mode = str(raw.get("cluster_label_mode") or "off")
    if cluster_label_mode not in {"off", "llm"}:
        raise ValueError("Fact cluster labels must be off or use the configured LLM.")
    cluster_similarity = max(-1.0, min(float(raw.get("cluster_minimum_similarity") or .78), 1.0))
    cluster_calls = max(0, min(int(raw.get("cluster_max_llm_calls") or 5), 50))
    schedule_labels = {
        0: ("Manual", "Started manually"),
        60: ("Every hour", "Scheduled · hourly"),
        360: ("Every 6 hours", "Scheduled · every 6 hours"),
        1440: ("Every day", "Scheduled · daily"),
        10080: ("Every week", "Scheduled · weekly"),
    }
    fetch_config = {
        "k": limit, "rotate": "facts", "query": query, "tag": tag,
        "source_ids": source_ids,
    }
    nodes = workflow_store.workflow_nodes(workflow_id)
    for node in nodes:
        if node.get("kind") == "trigger":
            node["label"], detail = schedule_labels[schedule]
            node["config"] = {"label": detail}
        elif node.get("kind") == "fetch_docs":
            node["config"] = fetch_config
            node["label"] = "Read configured document scope"
        elif node.get("kind") == "scan_facts":
            node["config"] = {
                "claims_per_document": claims, "instructions": instructions,
                "max_llm_calls": extraction_calls,
                "max_input_tokens": extraction_input,
                "max_output_tokens": extraction_output,
            }
        elif node.get("kind") == "map_fact_impact":
            node["config"] = {
                "retrieval_backend": retrieval_backend, "fact_neighbors": fact_neighbors,
                "evidence_neighbors": evidence_neighbors,
                "minimum_fact_similarity": fact_similarity,
                "minimum_evidence_similarity": evidence_similarity,
                "max_components": max_components,
            }
        elif node.get("kind") == "adjudicate_facts":
            node["config"] = {
                "mode": adjudication_mode, "max_calls": adjudication_calls,
                "max_input_tokens": adjudication_input,
                "max_output_tokens": adjudication_output,
                "output_tokens_per_call": min(4000, max(100, adjudication_output or 800)),
                "related_assertions": fact_neighbors, "evidence_spans": evidence_neighbors,
                "instructions": instructions,
            }
        elif node.get("kind") == "cluster_facts":
            node["config"] = {
                "label_mode": cluster_label_mode, "minimum_similarity": cluster_similarity,
                "max_llm_clusters": cluster_calls,
                "max_input_tokens": max(0, cluster_calls * 1600),
                "max_output_tokens": max(0, cluster_calls * 400),
                "instructions": instructions,
            }
        elif node.get("kind") == "review_facts":
            # The dialog has no threshold field, so whatever the workflow
            # already carries is the user's tuned value. Reconfiguring the
            # scan used to reset it to .85 every time.
            prior = (node.get("config") or {}).get("minimum_confidence")
            node["config"] = {
                "mode": review_mode,
                "minimum_confidence": max(0.0, min(float(prior or .85), 1.0)),
                "instructions": instructions,
            }
        elif node.get("kind") == "publish_facts":
            node["config"] = {"status": publish_status}
    workflow_store.update_nodes(workflow_id, nodes)
    workflow_store.set_trigger(
        workflow_id,
        {"on": "schedule", "every_minutes": schedule} if schedule else {"on": ""},
    )
    return {**fetch_config, "claims_per_document": claims, "schedule_minutes": schedule,
            "review_mode": review_mode, "review_instructions": instructions,
            "publish_status": publish_status, "retrieval_backend": retrieval_backend,
            "fact_neighbors": fact_neighbors, "evidence_neighbors": evidence_neighbors,
            "max_components": max_components,
            "minimum_fact_similarity": fact_similarity,
            "minimum_evidence_similarity": evidence_similarity,
            "extraction_max_calls": extraction_calls,
            "extraction_max_input_tokens": extraction_input,
            "extraction_max_output_tokens": extraction_output,
            "adjudication_mode": adjudication_mode,
            "adjudication_max_calls": adjudication_calls,
            "adjudication_max_input_tokens": adjudication_input,
            "adjudication_max_output_tokens": adjudication_output,
            "cluster_label_mode": cluster_label_mode,
            "cluster_minimum_similarity": cluster_similarity,
            "cluster_max_llm_calls": cluster_calls}


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
    workflow_store.quarantine_orphan_sync_workflows()
    for s in workflow_store.connector_sources():
        cfg = s["config"] if isinstance(s["config"], dict) else json.loads(s["config"] or "{}")
        project_access = access.external_access(
            int(s["project_id"]), str(s["project_slug"]), str(s["project_name"]),
            "automation", "connector-sync-seed",
            frozenset({"knowledge.read", "knowledge.write", "automation.run"}),
        )
        with access.use_access(project_access):
            ensure_sync_flow(s["id"], cfg.get("repo") or s["display_name"])
    for project in workflow_store.active_projects():
        project_access = access.external_access(
            int(project["id"]), str(project["slug"]), str(project["name"]),
            "automation", "fact-extraction-seed",
            frozenset({"knowledge.read", "knowledge.write", "automation.run"}),
        )
        with access.use_access(project_access):
            ensure_digest_flow()
            ensure_fact_scan_flow()
            # A deployment can change the embedding input contract without an
            # administrator touching Settings (for example, enabling Nomic's
            # asymmetric search tasks). Refresh stale chunks in the existing
            # guarded worker so the corpus does not silently stay keyword-only.
            from mari_server.persistence.postgres import document_index
            from mari_server.sources import sync as source_sync
            if document_index.needs_reindex():
                source_sync.start_reindex()

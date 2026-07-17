"""Mari Cloud — flow execution engine (FLOWS-DESIGN.md).

Executes a workflow's step pipeline for real: each step maps to an existing
platform primitive (hybrid search, LLM refine, fact check, tagging, link
derivation, site deploy, tasks, notifications). Runs execute on a background
thread; per-step status/duration persists to workflow_runs.rows_data so the UI
can poll live. Approval steps pause the run ('waiting'); approveRun resumes it.
"""

from __future__ import annotations

import fnmatch
import json
import threading
import time
import typing as t

import psycopg
from psycopg.rows import dict_row

import llm

DB_URL_REF: dict = {"url": "postgresql://localhost/mari_cloud"}


def _conn():
    return psycopg.connect(DB_URL_REF["url"], row_factory=dict_row)


def _now_label() -> str:
    return time.strftime("%b %d, %I:%M %p")


def _elapsed(start: float) -> str:
    s = int(time.time() - start)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ————— step implementations — each returns (status, detail, ctx_updates) —————


def _step_fetch(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    query, tag, k = cfg.get("query", ""), cfg.get("tag", ""), int(cfg.get("k", 3))
    # A triggered run operates on the documents that fired it.
    trigger_ids = ctx.get("trigger_doc_ids") or []
    with _conn() as conn:
        if trigger_ids:
            rows = conn.execute("SELECT id, title FROM documents WHERE id = ANY(%s) ORDER BY id",
                                (trigger_ids,)).fetchall()
        elif tag:
            rows = conn.execute(
                """SELECT d.id, d.title FROM documents d JOIN tags t ON t.document_id = d.id
                   WHERE t.tag = %s ORDER BY d.updated_src DESC NULLS LAST LIMIT %s""", (tag, k)).fetchall()
        elif query:
            rows = conn.execute(
                """SELECT id, title FROM documents
                   WHERE search_vec @@ plainto_tsquery('english', %s) OR title ILIKE %s
                   ORDER BY updated_src DESC NULLS LAST LIMIT %s""", (query, f"%{query}%", k)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title FROM documents ORDER BY updated_src DESC NULLS LAST LIMIT %s", (k,)).fetchall()
    ids = [r["id"] for r in rows]
    names = ", ".join(r["title"][:40] for r in rows[:3])
    src = " (from trigger)" if trigger_ids else ""
    return "passed", f"{len(ids)} documents{src} · {names}", {"doc_ids": ids}


def _step_refine(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    # late imports — flowengine must stay importable before app/db load
    from db import exec_, q1
    from mutations_knowledge import llm_refine
    skill = cfg.get("skill", "tighten")
    total = 0
    for doc_id in ctx.get("doc_ids", [])[:2]:  # cap LLM work per run
        doc = q1("SELECT * FROM documents WHERE id = %s", (doc_id,))
        if not doc:
            continue
        for original, replacement, reason in llm_refine(doc, skill):
            exec_("""INSERT INTO changes (document_id, original, replacement, reason)
                     VALUES (%s, %s, %s, %s) ON CONFLICT (document_id, original) DO NOTHING""",
                  (doc_id, original, replacement, reason))
            total += 1
    return "passed", f"{total} edits suggested ({skill})", {"edits": total}


def _step_fact_check(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from app import Mutation
    contradictions = 0
    checked = 0
    for doc_id in ctx.get("doc_ids", [])[:2]:
        contradictions += Mutation.fact_check(None, doc_id)  # type: ignore[arg-type]
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
    with _conn() as conn:
        n = 0
        for doc_id in ctx.get("doc_ids", []):
            conn.execute("INSERT INTO tags (document_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING", (doc_id, tag))
            n += 1
    return "passed", f"tagged {n} docs '{tag}'", {}


def _step_derive_links(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    from app import Mutation
    added = Mutation.derive_links(None)  # type: ignore[arg-type]
    return "passed", f"{added} new semantic links", {"links": added}


def _step_create_task(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    title = cfg.get("title", "Review flow output")
    if ctx.get("branch_taken") is False and cfg.get("only_if_branch"):
        return "skipped", "branch not taken", {}
    if ctx.get("dry_run"):
        return "passed", f"would create task: {title[:60]} (dry run)", {}
    with _conn() as conn:
        conn.execute("""INSERT INTO tasks (title, assignee, assignee_initials, assignee_tint, kind, kind_label)
                        VALUES (%s, %s, %s, 2, %s, %s) ON CONFLICT (title) DO NOTHING""",
                     (title[:120], cfg.get("assignee", "Aki K."),
                      "".join(w[0] for w in cfg.get("assignee", "Aki K.").split()[:2]).upper(),
                      cfg.get("kind", "factcheck"), cfg.get("kind_label", "Fact check")))
    return "passed", f"task: {title[:60]}", {}


def _step_approval(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    if ctx.get("dry_run"):
        return "passed", f"would await {cfg.get('assignee', 'Aki K.')} (dry run)", {}
    return "waiting", f"awaiting {cfg.get('assignee', 'Aki K.')}", {"pause": True}


def _step_deploy(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    if ctx.get("dry_run"):
        return "passed", "would deploy the site (dry run)", {}
    from app import Mutation
    version = Mutation.deploy_site(None, int(cfg.get("site_id", 1)))  # type: ignore[arg-type]
    return "passed", f"deployed {version}", {"version": version}


def _step_notify(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    if ctx.get("dry_run"):
        return "passed", f"would notify {cfg.get('user', 'Daniel Henneberger')} (dry run)", {}
    with _conn() as conn:
        conn.execute("""INSERT INTO notifications (user_name, kind, text, detail, at_label, read)
                        VALUES (%s, 'info', %s, %s, 'just now', false)
                        ON CONFLICT (user_name, text) DO NOTHING""",
                     (cfg.get("user", "Daniel Henneberger"),
                      cfg.get("text", "Flow finished")[:180],
                      cfg.get("detail", "")[:200]))
    return "passed", f"notified {cfg.get('user', 'Daniel Henneberger')}", {}


def _step_summarize(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT title, snippet FROM documents WHERE id = ANY(%s)",
                            (ctx.get("doc_ids", []),)).fetchall()
    text = llm.generate(
        "Summarize in 2 sentences:\n" + "\n".join(f"- {r['title']}: {r['snippet']}" for r in rows),
        system="You summarize document sets.") or f"{len(rows)} documents in scope."
    return "passed", text[:160], {"summary": text}


def _step_trigger(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    note = (ctx.get("trigger") or {}).get("note", "")
    return "passed", note or cfg.get("label", "manual run"), {}


def _step_sync_source(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Run the real diff-based ingest sync for one source, synchronously, and
    report honest per-step stats from the sync result."""
    import ingest  # late import — ingest imports flowengine at module load
    source_id = int(cfg.get("source_id") or 0)
    with _conn() as conn:
        src = conn.execute("SELECT display_name FROM sources WHERE id = %s", (source_id,)).fetchone()
    if not src:
        return "failed", f"source #{source_id} not found", {}
    name = src["display_name"]
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


def _step_refresh_digest(cfg: dict, ctx: dict) -> tuple[str, str, dict]:
    """Regenerate the weekly digest — same logic as the regenerateDigest mutation."""
    if ctx.get("dry_run"):
        return "passed", "would regenerate the weekly digest (dry run)", {}
    from app import Mutation  # late import to reuse app helpers
    ok = Mutation.regenerate_digest(None)  # type: ignore[arg-type]
    with _conn() as conn:
        n = conn.execute("SELECT count(*) AS n FROM digest_topics").fetchone()["n"]
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
    "deploy_site": _step_deploy,
    "notify": _step_notify,
    "summarize": _step_summarize,
    "sync_source": _step_sync_source,
    "refresh_digest": _step_refresh_digest,
}

# steps that call the local LLM (shown as slow in the UI)
LLM_STEPS = {"refine", "fact_check", "derive_links", "summarize", "refresh_digest"}


def _persist(run_id: int, rows: list[dict], status: str, progress: int, stats: dict, start: float) -> None:
    with _conn() as conn:
        conn.execute("""UPDATE workflow_runs SET rows_data = %s, status = %s, progress = %s,
                        stats = %s, duration = %s WHERE id = %s""",
                     (json.dumps(rows), status, progress, json.dumps(stats), _elapsed(start), run_id))


def execute_run(run_id: int, resume_from: int = 0) -> None:
    """Run (or resume) a workflow's steps sequentially, persisting after each."""
    with _conn() as conn:
        run = conn.execute("SELECT * FROM workflow_runs WHERE id = %s", (run_id,)).fetchone()
        wf = conn.execute("SELECT * FROM workflows WHERE id = %s", (run["workflow_id"],)).fetchone()
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
        impl = STEP_IMPLS.get(kind)
        t0 = time.time()
        try:
            status, detail, updates = impl(step.get("config", {}), ctx) if impl else ("failed", f"unknown step: {kind}", {})
        except Exception as e:
            status, detail, updates = "failed", f"{type(e).__name__}: {e}"[:140], {}
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
    return {"contradictions": ctx.get("contradictions", 0),
            "edits": ctx.get("edits", 0), "links": ctx.get("links", 0)}


def start_run(run_id: int, resume_from: int = 0) -> None:
    threading.Thread(target=execute_run, args=(run_id, resume_from), daemon=True).start()


# ————— document triggers (init.sql: workflows.trigger jsonb) —————
#
# Trigger shape: {"on": "document_changed"|"document_added"|"", "source_id": int|null,
#                 "tag": str|null, "path_glob": str|null}. Empty "on" = manual-only.


def _trigger_matches(trig: dict, change: str, docs: list[dict], doc_tags: dict[int, set]) -> list[dict]:
    """Return the subset of changed docs this trigger matches (ANDed filters)."""
    if (trig.get("on") or "") != change:
        return []
    matched = []
    for d in docs:
        if trig.get("source_id") is not None and d.get("source_id") != int(trig["source_id"]):
            continue
        if trig.get("tag") and trig["tag"] not in doc_tags.get(d["id"], set()):
            continue
        if trig.get("path_glob") and not fnmatch.fnmatch(d.get("source_path") or "", trig["path_glob"]):
            continue
        matched.append(d)
    return matched


def fire_document_triggers(doc_ids: list[int], change: str) -> list[int]:
    """Start real runs for every active workflow whose trigger matches this
    change batch. Each workflow fires at most once per batch (debounce), with
    the matching docs injected as ctx.trigger_doc_ids so _step_fetch operates
    on them. Returns the started run ids."""
    if not doc_ids or change not in ("document_changed", "document_added"):
        return []
    with _conn() as conn:
        docs = conn.execute(
            "SELECT id, title, source_id, source_path FROM documents WHERE id = ANY(%s)",
            (doc_ids,)).fetchall()
        wfs = conn.execute(
            """SELECT id, name, trigger FROM workflows
               WHERE status = 'active' AND trigger->>'on' = %s ORDER BY id""", (change,)).fetchall()
        if not docs or not wfs:
            return []
        doc_tags: dict[int, set] = {}
        for r in conn.execute("SELECT document_id, tag FROM tags WHERE document_id = ANY(%s)",
                              ([d["id"] for d in docs],)).fetchall():
            doc_tags.setdefault(r["document_id"], set()).add(r["tag"])

        run_ids: list[int] = []
        verb = "updated" if change == "document_changed" else "added"
        for wf in wfs:  # one pass over the batch = fires each workflow at most once
            trig = wf["trigger"] if isinstance(wf["trigger"], dict) else json.loads(wf["trigger"] or "{}")
            matched = _trigger_matches(trig, change, docs, doc_tags)
            if not matched:
                continue
            first = matched[0].get("source_path") or matched[0]["title"]
            note = f"Triggered by: {first} {verb}" + (f" (+{len(matched) - 1} more)" if len(matched) > 1 else "")
            trigger_meta = {"on": change, "doc_ids": [d["id"] for d in matched], "note": note}
            n = conn.execute("SELECT coalesce(max(number), 1800) + 1 AS n FROM workflow_runs WHERE workflow_id = %s",
                             (wf["id"],)).fetchone()["n"]
            row = conn.execute(
                """INSERT INTO workflow_runs (workflow_id, number, status, started_label, duration,
                                              progress, stats, rows_data, triggered_by)
                   VALUES (%s, %s, 'running', to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]', %s)
                   RETURNING id""",
                (wf["id"], n,
                 json.dumps({"ctx": {"trigger_doc_ids": [d["id"] for d in matched], "trigger": trigger_meta},
                             "trigger": trigger_meta}),
                 note)).fetchone()
            conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                         ("Flow trigger", f"auto-started run #{n} ({note[:80]})", wf["name"]))
            conn.commit()
            run_ids.append(row["id"])
        for rid in run_ids:
            start_run(rid)
        return run_ids


# ————— schedule triggers (init.sql: workflow_runs.started_at) —————
#
# Trigger shape: {"on": "schedule", "every_minutes": N} (1..10080). A daemon
# thread scans every 30s and starts runs that are due; "last started" is
# derived from the latest run's started_at — no per-workflow state to keep.

SCHEDULER_TICK_SECONDS = 30
_SCHEDULER = {"started": False}
PROCESS_START_TS = time.time()  # for startup reconciliation — never touch newer rows


def reconcile_stale_runs() -> int:
    """Startup reconciliation: a run left 'running'/'waiting' by an unclean
    shutdown blocks run_due_schedules forever (it sees the latest run as still
    in progress). Mark runs started BEFORE this process as failed; runs this
    process started are never touched. Returns how many were flipped."""
    with _conn() as conn:
        rows = conn.execute(
            """UPDATE workflow_runs
               SET status = 'failed',
                   stats = coalesce(stats, '{}'::jsonb) || '{"note": "interrupted by restart"}'::jsonb
               WHERE status IN ('running', 'waiting') AND started_at < to_timestamp(%s)
               RETURNING id""", (PROCESS_START_TS,)).fetchall()
        if rows:
            conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                         ("Flow scheduler", f"marked {len(rows)} stale run(s) failed (interrupted by restart)",
                          "startup reconciliation"))
        conn.commit()
    return len(rows)


def run_due_schedules() -> list[int]:
    """One scheduler pass: start a run for every active schedule-triggered
    workflow that is due (now - last started >= every_minutes). A workflow
    whose latest run is still running/waiting is never double-started.
    Returns the started run ids."""
    started: list[int] = []
    with _conn() as conn:
        wfs = conn.execute(
            """SELECT id, name, trigger FROM workflows
               WHERE status = 'active' AND trigger->>'on' = 'schedule' ORDER BY id""").fetchall()
        for wf in wfs:
            trig = wf["trigger"] if isinstance(wf["trigger"], dict) else json.loads(wf["trigger"] or "{}")
            try:
                every = int(trig.get("every_minutes") or 0)
            except (TypeError, ValueError):
                continue
            if every < 1:
                continue
            last = conn.execute(
                """SELECT status, (now() - started_at) >= make_interval(mins => %s) AS due
                   FROM workflow_runs WHERE workflow_id = %s ORDER BY id DESC LIMIT 1""",
                (every, wf["id"])).fetchone()
            if last and last["status"] in ("running", "waiting"):
                continue  # never double-start while a run is in progress
            if last and not last["due"]:
                continue  # not yet due (no prior run = due immediately)
            label = f"Scheduled · every {every} min"
            trigger_meta = {"on": "schedule", "every_minutes": every, "note": label}
            n = conn.execute("SELECT coalesce(max(number), 1800) + 1 AS n FROM workflow_runs WHERE workflow_id = %s",
                             (wf["id"],)).fetchone()["n"]
            row = conn.execute(
                """INSERT INTO workflow_runs (workflow_id, number, status, started_label, duration,
                                              progress, stats, rows_data, triggered_by)
                   VALUES (%s, %s, 'running', to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, %s, '[]', %s)
                   RETURNING id""",
                (wf["id"], n, json.dumps({"ctx": {"trigger": trigger_meta}, "trigger": trigger_meta}),
                 label)).fetchone()
            conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                         ("Flow scheduler", f"auto-started run #{n} ({label})", wf["name"]))
            conn.commit()
            started.append(row["id"])
    for rid in started:
        start_run(rid)
    return started


def start_scheduler() -> None:
    """Start the schedule-trigger daemon thread (guarded against uvicorn
    --reload / repeat imports double-starting it)."""
    if _SCHEDULER["started"]:
        return
    _SCHEDULER["started"] = True

    # Reconcile BEFORE the loop begins: runs wedged in 'running'/'waiting' by
    # an unclean shutdown would otherwise block their schedule forever.
    try:
        reconcile_stale_runs()
    except Exception:  # noqa: BLE001 — reconciliation must never block startup
        pass

    def loop() -> None:
        while True:
            time.sleep(SCHEDULER_TICK_SECONDS)
            try:
                run_due_schedules()
            except Exception:  # noqa: BLE001 — the scheduler must survive transient DB errors
                pass

    threading.Thread(target=loop, daemon=True, name="flow-scheduler").start()


# ————— seeded scheduled flows (startup + connectGithubRepo) —————


def _wf_nodes(row: dict) -> list[dict]:
    return row["nodes"] if isinstance(row["nodes"], list) else json.loads(row["nodes"] or "[]")


def ensure_sync_flow(source_id: int, repo: str) -> int | None:
    """Idempotently create the scheduled 'Sync <label>' flow for a github or
    connector source. Returns the new workflow id, or None if one exists."""
    with _conn() as conn:
        for r in conn.execute("SELECT id, nodes FROM workflows").fetchall():
            if any(s.get("kind") == "sync_source" and
                   int((s.get("config") or {}).get("source_id") or 0) == source_id
                   for s in _wf_nodes(r)):
                return None
        nodes = [
            {"kind": "trigger", "label": "Every 10 min", "config": {"label": "Scheduled · every 10 min"}},
            {"kind": "sync_source", "label": f"Sync {repo}", "config": {"source_id": source_id}},
        ]
        row = conn.execute(
            """INSERT INTO workflows (name, description, color, pinned, status, nodes, trigger)
               VALUES (%s, %s, '#5c7a4c', false, 'active', %s, %s) RETURNING id""",
            (f"Sync {repo}"[:120], f"Keeps {repo} indexed — incremental sync on a schedule.",
             json.dumps(nodes), json.dumps({"on": "schedule", "every_minutes": 10}))).fetchone()
        conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                     ("Flow scheduler", "created scheduled sync flow", f"Sync {repo}"))
        conn.commit()
        return row["id"]


def ensure_digest_flow() -> int | None:
    """Idempotently create the 'Weekly digest refresh' flow (schedule: every
    10080 min == weekly). The old settings.digest_schedule.enabled decides
    whether it starts active or paused. Returns the new id or None."""
    with _conn() as conn:
        for r in conn.execute("SELECT id, nodes FROM workflows").fetchall():
            if any(s.get("kind") == "refresh_digest" for s in _wf_nodes(r)):
                return None
        st = conn.execute("SELECT value FROM settings WHERE key = 'digest_schedule'").fetchone()
        val = (st or {}).get("value") or {}
        if isinstance(val, str):
            val = json.loads(val or "{}")
        status = "active" if val.get("enabled", True) else "paused"
        nodes = [
            {"kind": "trigger", "label": "Every week", "config": {"label": "Scheduled · weekly"}},
            {"kind": "refresh_digest", "label": "Refresh weekly digest", "config": {}},
        ]
        row = conn.execute(
            """INSERT INTO workflows (name, description, color, pinned, status, nodes, trigger)
               VALUES (%s, %s, '#c8973a', false, %s, %s, %s) RETURNING id""",
            ("Weekly digest refresh", "Regenerates the Overview digest from recent documents and facts.",
             status, json.dumps(nodes), json.dumps({"on": "schedule", "every_minutes": 10080}))).fetchone()
        conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                     ("Flow scheduler", "created scheduled digest flow", "Weekly digest refresh"))
        conn.commit()
        return row["id"]


def seed_scheduled_flows() -> None:
    """Startup seeding: every github/connector source gets a scheduled sync
    flow; the weekly digest gets a refresh flow. Idempotent — existing kept."""
    with _conn() as conn:
        srcs = conn.execute(
            "SELECT id, display_name, config FROM sources WHERE kind IN ('github', 'connector')").fetchall()
    for s in srcs:
        cfg = s["config"] if isinstance(s["config"], dict) else json.loads(s["config"] or "{}")
        ensure_sync_flow(s["id"], cfg.get("repo") or s["display_name"])
    ensure_digest_flow()

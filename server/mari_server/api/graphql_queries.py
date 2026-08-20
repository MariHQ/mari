"""Mari — GraphQL Query root, hybrid search, and lineage layout.

DESIGN.md §4–§5: hybrid search = tsvector rank + pgvector cosine, boosted by
tag weights (tag_definitions.search_weight).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
import time
import typing as t

import strawberry
import numpy as np
from strawberry.scalars import JSON

from mari_server import config
from mari_server.api import access
from mari_server.integrations import github
from mari_server.services import sync as ingest
from mari_server.integrations import llm
from mari_server.integrations import vector_index as retrieval
from mari_server.services import review as review_application
from mari_server.repositories import review_repository
from mari_server.repositories.database import actor_name, exec_, jload, q, q1
from mari_server.services.excerpt import excerpt
from mari_server.api.graphql_types import (
    ActivityBucket, ActivityItem, ApiKey, ApprovedAnswer,
    AuditDetail, AuditEvent, AuditFinding, AuditRun, Change, ChatMessage,
    ChatSession, Checkpoint, Decision, DigestImpact, DigestTopic, DigestWhere,
    DocHistory, Document, DocumentTemplate, Fact, FactContradiction, Finding,
    FreshnessRow, GithubRepo, GithubTeamSync, GlossaryCandidate, GlossaryTerm,
    GraphStats, GraphView, InsightStats, LineageEdge, LineageNode, McpServer,
    Member, Notification, Provisioning, ReadabilityRow, RelatedDoc, Setting,
    KnowledgeChatDestination, SourcePulse, StyleGuide, StyleRule, SyncEvent,
    SyncStatus, TagDef, Task, TaskSummary, ReviewConnection, ReviewItem, ReviewPageInfo,
    TopCited, UploadFile, UploadManifest,
    Trajectory, TrajectoryStep, VoiceLayer, Workflow, WorkflowRun, Workspace,
)

# ————————————————— lineage layout (LINEAGE-DESIGN.md §3.3) —————————————————

LANES = {"github": (0.14, 0.30), "slack": (0.32, 0.46), "docs": (0.48, 0.66),
         "notion": (0.68, 0.80)}
LANE_OTHER = (0.82, 0.90)
SOURCE_ICONS = {"github": "github", "slack": "slack", "docs": "doc", "notion": "notion"}


def _iso_date_arg(value: str | None) -> str | None:
    """An ISO date (YYYY-MM-DD) argument, or None. Anything that is not a date
    is rejected rather than silently ignored: a range control whose bound the
    server quietly dropped would relabel numbers it did not change."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v[:10]).isoformat()
    except ValueError as e:
        raise ValueError(f"{value!r} is not an ISO date (YYYY-MM-DD)") from e


def _hash01(s: str) -> float:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16) % 1000 / 1000


def layout_nodes(rows: list[dict]) -> dict[int, tuple[float, float]]:
    """Deterministic auto-layout: x = time, y = source lane + stable hash offset,
    then a collision pass. Pinned nodes keep graph_x/graph_y verbatim."""
    dates = [r["updated_src"] for r in rows if r["updated_src"]]
    lo, hi = (min(dates), max(dates)) if dates else (dt.date.today(), dt.date.today())
    span = max((hi - lo).days, 1)
    pos, pinned = {}, set()
    for r in rows:
        if r["graph_x"] is not None:
            pos[r["id"]] = (float(r["graph_x"]), float(r["graph_y"] or 0.5))
            pinned.add(r["id"])
        else:
            x = 0.24 + ((r["updated_src"] or lo) - lo).days / span * (0.86 - 0.24)
            y0, y1 = LANES.get(r["source"], LANE_OTHER)
            pos[r["id"]] = (x, y0 + _hash01(r["external_id"]) * (y1 - y0))
    # Collision pass: move each colliding unpinned node (id order) to the nearest
    # free y on a fine grid, clear of everything already placed. Deterministic;
    # pinned-pinned overlaps stand (user's choice).
    placed = sorted(pinned)
    for b in sorted(k for k in pos if k not in pinned):
        bx, by = pos[b]

        def free(yv: float) -> bool:  # 1e-9 slack absorbs float grid error
            return all(abs(pos[a][0] - bx) >= 0.16 - 1e-9 or abs(pos[a][1] - yv) >= 0.09 - 1e-9
                       for a in placed)

        if not free(by):
            cands = sorted((0.13 + k * 0.01 for k in range(78)), key=lambda v: (abs(v - by), v))
            pos[b] = (bx, next((c for c in cands if free(c)), by))
        placed.append(b)
    return pos


def classify_node(row: dict) -> tuple[str, str]:
    """LINEAGE-ROLLUP-CONTRACT.md §1 — (docKind, group) from source_path +
    source kind. Legacy seed docs (no source_id) → ("seed", ""). Markdown
    pages are never grouped; commits/prs/issues group per repo."""
    if row.get("source_id") is None:
        return "seed", ""
    sp = row.get("source_path") or ""
    kind = row.get("kind") or "page"
    if sp.startswith("issues/"):
        doc_kind = "issue"
    elif sp.startswith("pulls/"):
        doc_kind = "pr"
    elif sp.startswith("commits/"):
        doc_kind = "commit"
    elif kind in ("answer", "decision"):
        doc_kind = kind
    else:
        doc_kind = "page"
    if doc_kind in ("commit", "pr", "issue") and row.get("src_kind") == "github" and row.get("repo"):
        return doc_kind, f"gh:{row['repo']}:{doc_kind}s"
    return doc_kind, ""


def _doc(row: dict, watched: bool = False) -> Document:
    return Document(
        id=row["id"], source=row["source"], title=row["title"],
        # The card's sentence, built from the body when the row carries one
        # (excerpt.py). Rows ingested before excerpt existed stored the raw
        # first 180 characters, Markdown preamble and all; recomputing here
        # renders them the same as new rows without a backfill.
        snippet=excerpt(row.get("body"), row["title"]) if row.get("body") else row["snippet"],
        body=row.get("body", ""), kind=row.get("kind", "page"), author=row["author"], author_initials=row["author_initials"],
        # ISO 8601, NOT a display string. The console formats dates itself
        # (components/tokens/format.ts fmtDate) and sorts columns on the raw
        # value, so "Jul 8, 2026" would both double-format and sort
        # alphabetically. The API returns data; the client renders it.
        date=row["updated_src"].isoformat() if row["updated_src"] else "",
        tags=row.get("tags") or [], watched=watched,
    )


# A page of documents ordered by recency. `d.id DESC` is not decoration:
# updated_src is a DATE, so a corpus ingested in one sync shares one value
# across thousands of rows and the sort between them is whatever the plan
# happens to produce. Paging an unstable sort duplicates some rows and skips
# others (SRCH-2), and the reader has no way to tell.
DOC_SQL = """
  SELECT d.id, d.source, d.external_id, d.title, d.snippet, d.body, d.author,
         d.author_initials, d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags
  FROM documents d LEFT JOIN tags t ON t.document_id = d.id
  {where}
  GROUP BY d.id ORDER BY d.updated_src DESC NULLS LAST, d.id DESC
"""

# Search implementation lives behind the infrastructure boundary.
from mari_server.services.search import MAX_K, hybrid_count, hybrid_search, invalidate_search, like_pattern

# ————————————————— search telemetry (SRCH-4) —————————————————
#
# The console pages by re-issuing the same query with a larger k, so logging on
# every call recorded one row per "show more" and `insightStats.searches` grew
# with scrolling rather than with searching. A query is logged once per window
# per person: the second page of a search is the same search.
#
# The dedupe is a NOT EXISTS on the insert, not a read-then-write, so two
# concurrent pages of the same query cannot both find nothing and both insert.
SEARCH_LOG_WINDOW = "5 minutes"


def log_search_once(query: str) -> None:
    """Record that someone searched for `query` — unless the same person
    already searched for it inside the window, in which case this is a later
    page of that search and there is nothing new to record."""
    detail = query[:120]
    try:
        exec_(f"""
          INSERT INTO usage_log (kind, detail)
          SELECT 'search', %s
           WHERE NOT EXISTS (SELECT 1 FROM usage_log
                              WHERE kind = 'search' AND detail = %s
                                AND at > now() - interval '{SEARCH_LOG_WINDOW}')""",
              (detail, detail))
    except Exception:  # noqa: BLE001 — telemetry never breaks the search it rides on
        pass


# ————————————————— fact contradiction detection —————————————————
# Deterministic, no model call. Two stored claims contradict when they are
# about the same thing — their non-numeric, non-negating words overlap almost
# completely — but they disagree on either the numbers they state or on
# polarity (one negates, the other does not). Both sides of every pair are
# real `facts` rows: the detector pairs claims, it never writes one, and a
# ledger with no such pair yields an empty list rather than a warning.
#
# The overlap bar is deliberately high. A missed contradiction shows up again
# the next time someone reads the two claims; a false one trains the team to
# dismiss the banner.

_NEGATIONS = {"not", "no", "never", "cannot", "can't", "cant", "won't", "wont",
              "doesn't", "doesnt", "don't", "dont", "isn't", "isnt", "aren't",
              "arent", "without", "disabled", "unsupported", "unavailable"}
_STOP = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
         "in", "on", "for", "and", "or", "it", "its", "this", "that", "these",
         "we", "our", "us", "by", "at", "as", "with", "from", "has", "have"}
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'’\-]*")

# A pair must share at least this many topic words, and this fraction of their
# combined vocabulary, before a numeric or polarity difference counts.
_MIN_SHARED = 3
_MIN_OVERLAP = 0.8


def _claim_shape(claim: str) -> tuple[frozenset[str], tuple[str, ...], bool]:
    """(topic words, the numbers stated, whether the claim negates)."""
    text = claim.lower().replace("’", "'")
    numbers = tuple(sorted(n.replace(",", "") for n in _NUM_RE.findall(text)))
    words = _WORD_RE.findall(_NUM_RE.sub(" ", text))
    negated = any(w in _NEGATIONS for w in words)
    topic = frozenset(w for w in words if w not in _STOP and w not in _NEGATIONS)
    return topic, numbers, negated


def detect_contradictions(rows: list[dict]) -> list[tuple[dict, dict, str, str]]:
    """(fact, other fact, reason, detail) for every contradicting pair."""
    shaped = [(r, *_claim_shape(r["claim"])) for r in rows]
    out: list[tuple[dict, dict, str, str]] = []
    for i, (a, topic_a, nums_a, neg_a) in enumerate(shaped):
        for b, topic_b, nums_b, neg_b in shaped[i + 1:]:
            shared, union = topic_a & topic_b, topic_a | topic_b
            if len(shared) < _MIN_SHARED or not union:
                continue
            if len(shared) / len(union) < _MIN_OVERLAP:
                continue
            if nums_a != nums_b:
                reason = "numeric"
                detail = f"{' / '.join(nums_a) or 'no figure'} vs {' / '.join(nums_b) or 'no figure'}"
            elif neg_a != neg_b:
                reason = "polarity"
                detail = "one claim negates what the other asserts"
            else:
                continue
            out.append((a, b, reason, detail))
    return out


# ————————————————— secret masking —————————————————
# Settings rows and MCP tokens carry credentials; the GraphQL read path never
# returns them verbatim. Masked shape: "••••…last4" plus a *_set boolean so the
# Bots setup UI can still show "configured" without the secret.

_SECRET_SETTING_FIELDS = {
    "slack_bot": ("bot_token", "signing_secret"),
    "github_bot": ("webhook_secret",),
}


def _mask_secret(v) -> str:
    if not isinstance(v, str) or not v:
        return ""
    return "••••…" + v[-4:] if len(v) > 8 else "••••"


def _details(row: dict) -> list[AuditDetail]:
    """events.detail (a jsonb array of {label,value}) as GraphQL rows. Rows
    written before the column existed decode to [], which is honest: nothing
    beyond actor/verb/target/at was recorded for them."""
    out = []
    for d in jload(row.get("detail")) or []:
        if isinstance(d, dict) and d.get("label"):
            out.append(AuditDetail(label=str(d["label"]), value=str(d.get("value", ""))))
    return out


def _audit_where(query: str, date_from: str | None, date_to: str | None) -> tuple[str, tuple]:
    """The access log's filter, as one predicate. Shared by `audit_log` and
    `audit_log_total` so the count and the rows can never describe different
    filters."""
    clauses, args = [], []
    text = (query or "").strip()
    if text:
        clauses.append("(actor ILIKE %s OR verb ILIKE %s OR target ILIKE %s)")
        args += [f"%{text}%"] * 3
    floor, ceil = _iso_date_arg(date_from), _iso_date_arg(date_to)
    if floor:
        clauses.append("occurred_at >= %s")
        args.append(floor)
    if ceil:
        # Inclusive of the whole end day: the filter names dates, not instants.
        clauses.append("occurred_at < (%s::date + 1)")
        args.append(ceil)
    return (" AND ".join(clauses) if clauses else "true"), tuple(args)


def _mask_setting(key: str, value):
    if key == "setup_token":
        return _mask_secret(value if isinstance(value, str) else json.dumps(value))
    if key == "llm" and isinstance(value, dict) and isinstance(value.get("keys"), dict):
        # Provider API keys live one level down and were coming back verbatim.
        # The console only ever shows whether a key is set and its last four,
        # so that is all this returns.
        value = dict(value)
        value["keys"] = {k: _mask_secret(v) for k, v in value["keys"].items()}
    if key == "llm" and isinstance(value, dict) and isinstance(value.get("gateway"), dict):
        value = dict(value)
        value["gateway"] = llm.mask_gateway_secrets(value["gateway"], _mask_secret)
    fields = _SECRET_SETTING_FIELDS.get(key)
    if not fields or not isinstance(value, dict):
        return value
    out = dict(value)
    for f in fields:
        secret = out.pop(f, None)
        out[f"{f}_set"] = bool(secret)
        if secret:
            out[f"{f}_hint"] = _mask_secret(secret)
    return out


def _effective_model_setting(key: str, value):
    """Overlay deployment-owned model selection on the editable workspace row.

    The runtime already gives environment/TOML configuration precedence over
    database settings. Returning the raw row made Settings claim Ollama was
    active while the process was actually using a gateway and local Sentence
    Transformers. The console must describe the process that will answer the
    next request, not the seed values left in PostgreSQL.
    """
    if key not in {"llm", "embedding"}:
        return value
    out = dict(value) if isinstance(value, dict) else {}
    provider, model = llm.generation_model() if key == "llm" else llm.embedding_model()
    if provider and model:
        out.update({"provider": provider, "model": model})
    if key == "llm" and provider == "gateway":
        out["gateway"] = llm.gateway_config()
    return out


# ————————————————— Query —————————————————


@strawberry.type
class Query:
    @strawberry.field
    def trajectories(self, limit: int = 50, offset: int = 0,
                     category: str | None = None) -> list[Trajectory]:
        """Newest harvested agent workflows, bounded for progressive rendering."""
        cap, start = max(1, min(int(limit), 100)), max(0, int(offset))
        args: list = []
        where = ""
        if (category or "").strip():
            where = "WHERE category = %s"
            args.append(category.strip())
        args.extend((cap, start))
        rows = q(f"SELECT * FROM trajectories {where} ORDER BY started_at DESC, id DESC LIMIT %s OFFSET %s",
                 tuple(args))
        if not rows:
            return []
        by_id: dict[int, list[TrajectoryStep]] = {int(row["id"]): [] for row in rows}
        for step in q("""SELECT trajectory_id, ordinal, tool, action_family, args, summary, ok
                           FROM trajectory_steps WHERE trajectory_id = ANY(%s)
                           ORDER BY trajectory_id, ordinal""", (list(by_id),)):
            by_id[int(step["trajectory_id"])].append(TrajectoryStep(
                ordinal=int(step["ordinal"]), tool=step["tool"], action_family=step["action_family"],
                args=jload(step["args"]) or {}, summary=step["summary"], ok=bool(step["ok"])))
        return [Trajectory(
            id=int(row["id"]), session_id=row.get("session_id"), prompt=row["prompt"],
            status=row["status"], model=row["model"], layer1=row["layer1"], layer2=row["layer2"],
            category=row["category"], macro_intent=row["macro_intent"], phases=jload(row["phases"]) or [],
            step_count=int(row["step_count"]), failure_count=int(row["failure_count"]),
            rework_count=int(row["rework_count"]), started_at=row["started_at"].isoformat(),
            completed_at=row["completed_at"].isoformat() if row.get("completed_at") else "",
            steps=by_id[int(row["id"])]) for row in rows]

    @strawberry.field
    def trajectory_total(self, category: str | None = None) -> int:
        if (category or "").strip():
            return int(q1("SELECT count(*) AS n FROM trajectories WHERE category = %s",
                          (category.strip(),))["n"])
        return int(q1("SELECT count(*) AS n FROM trajectories")["n"])

    @strawberry.field
    def trajectory_categories(self) -> list[str]:
        return [row["category"] for row in q(
            "SELECT category FROM trajectories GROUP BY category ORDER BY count(*) DESC, category")]

    @strawberry.field
    def overview_stats(self, since: str | None = None) -> JSON:
        """`since` is an ISO date: the lower bound of the window the dashboard's
        range picker is on. It bounds `changes` — rows in the `changes` table,
        which is what a tile labelled "changes" has to be counting. It used to
        count `events`, the access log, so the number moved when somebody signed
        in and stood still when the drift checker proposed a hundred edits
        (STATS-3).

        The bound is `>=`, closed, the same shape `insightStats` uses. It was
        `>` on the default window and `>=` on the picked one, so the same
        dashboard counted its two windows by two different rules.

        The other three are gauges of the workspace right now (facts awaiting
        review, runs in flight, active flows) and have no window to count over."""
        floor = _iso_date_arg(since)
        if floor:
            changes = q1("SELECT count(*) AS n FROM changes WHERE created_at >= %s", (floor,))["n"]
        else:
            changes = q1("SELECT count(*) AS n FROM changes WHERE created_at >= now() - interval '7 days'")["n"]
        facts_review = q1("SELECT count(*) AS n FROM facts WHERE status <> 'Verified'")["n"]
        running = q1("SELECT count(*) AS n FROM workflow_runs WHERE status IN ('running','waiting')")["n"]
        flows = q1("SELECT count(*) AS n FROM workflows WHERE status = 'active'")["n"]
        return {"changes": int(changes), "factsReview": int(facts_review),
                "flowsRunning": int(running), "flowsActive": int(flows)}

    @strawberry.field
    def source_pulse(self) -> list[SourcePulse]:
        # bars = real per-source doc-change counts by day (last 12 days, empty
        # days = 0), from documents timestamps; [] when a source has no recent
        # activity — never an invented curve.
        daily = q("""SELECT source_id, updated_src AS day, count(*) AS n FROM documents
                     WHERE source_id IS NOT NULL AND updated_src >= current_date - 11
                     GROUP BY source_id, updated_src""")
        per_src: dict[int, dict] = {}
        for d in daily:
            per_src.setdefault(d["source_id"], {})[d["day"]] = int(d["n"])
        today = dt.date.today()

        def bars(source_id: int) -> list[int]:
            days = per_src.get(source_id)
            if not days:
                return []
            return [days.get(today - dt.timedelta(days=11 - i), 0) for i in range(12)]

        def safe_config(r: dict) -> dict:
            """Never leak connector credentials: secret field values are masked
            and bulky internal hash maps dropped (CONNECTORS-CONTRACT.md)."""
            from mari_server.services import connector_sync
            cfg = jload(r["config"]) or {}
            if r.get("kind") == "connector":
                return connector_sync.masked_config(r["provider"], cfg)
            return {k: v for k, v in cfg.items() if k != "shas"}

        # A source's automatic cadence is not a column on `sources`: it is the
        # trigger of the "Sync <name>" flow flowengine.ensure_sync_flow creates
        # for it, keyed by the sync_source step's source_id. Read it back the
        # same way, so the Sources card and the Flows editor can only ever show
        # one cadence for one source.
        schedules: dict[int, tuple[int, int | None]] = {}
        for w in q("SELECT id, status, nodes, trigger FROM workflows"):
            trig = jload(w["trigger"]) or {}
            every = trig.get("every_minutes") if trig.get("on") == "schedule" else None
            for step in jload(w["nodes"]) or []:
                if not isinstance(step, dict) or step.get("kind") != "sync_source":
                    continue
                sid = int((step.get("config") or {}).get("source_id") or 0)
                if sid:
                    # Paused flow = no automatic sync, which is what None says.
                    active = w["status"] == "active" and isinstance(every, int)
                    schedules[sid] = (w["id"], int(every) if active else None)

        return [
            SourcePulse(id=r["id"], provider=r["provider"], name=r["display_name"], status=r["status"],
                        stat=r["stat_num"], unit=r["stat_unit"], bars=bars(r["id"]),
                        docs_count=r["docs_count"], health=r["health"], config=safe_config(r),
                        kind=r.get("kind") or "",
                        last_sync_at=r["last_sync_at"].isoformat() if r.get("last_sync_at") else "",
                        sync_flow_id=schedules.get(r["id"], (None, None))[0],
                        sync_interval_minutes=schedules.get(r["id"], (None, None))[1])
            for r in q("SELECT * FROM sources ORDER BY id")
        ]

    @strawberry.field
    def github_repos(self) -> list[GithubRepo]:
        """Repos visible to the configured token; [] (never errors) without a token."""
        source = q1(
            """SELECT config FROM sources
               WHERE kind = 'connector' AND split_part(provider, ':', 1) = 'github'
                 AND config->>'token' <> ''
               ORDER BY id DESC LIMIT 1"""
        )
        source_cfg = jload(source["config"]) if source else {}
        available_token = github.configured_token() or str((source_cfg or {}).get("token") or "")
        if not available_token:
            return []
        try:
            repos = github.repositories(available_token)
        except Exception:
            return []
        connected = {jload(r["config"]).get("repo", "")
                     for r in q("""SELECT config FROM sources WHERE kind = 'connector'
                                   AND split_part(provider, ':', 1) = 'github'""")}
        return [GithubRepo(
            full_name=r["full_name"], description=r.get("description") or "",
            private=bool(r.get("private")), default_branch=r.get("default_branch") or "main",
            updated_at=r.get("updated_at") or "", connected=r["full_name"] in connected,
        ) for r in repos]

    @strawberry.field
    def sync_status(self, source_id: int) -> SyncStatus:
        project_id = access.require_current_access().project_id
        live = ingest.status(source_id)
        src = q1("""SELECT kind, config, last_sync_at FROM sources
                    WHERE project_id = %s AND id = %s""", (project_id, source_id))
        cfg = jload(src["config"]) if src else {}
        # Connector cursors are provider-native and shown as bounded text.
        cursor = cfg.get("cursor") or ""
        counts = q1("""
          SELECT count(DISTINCT d.id) AS docs, count(c.id) AS chunks,
                 count(c.id) FILTER (WHERE c.embedding IS NOT NULL) AS embedded
          FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
          WHERE d.project_id = %s AND d.source_id = %s""", (project_id, source_id)) or {"docs": 0, "chunks": 0, "embedded": 0}
        last_sync = src["last_sync_at"].isoformat() if src and src["last_sync_at"] else (cfg.get("last_sync_at") or "")
        return SyncStatus(
            state=live["state"], phase=live["phase"], done=live["done"], total=live["total"],
            last_sync_at=last_sync,
            last_error=live["error"] or cfg.get("last_error", ""),
            cursor=cursor[:40],
            doc_count=int(counts["docs"]), chunk_count=int(counts["chunks"]),
            embedded_count=int(counts["embedded"]))

    @strawberry.field
    def search(self, query: str = "", k: int = 10, offset: int = 0) -> list[Document]:
        """One page of results. `offset` is a real SQL offset, so a browser can
        walk a corpus larger than one page instead of the caller pretending the
        first k rows are all there is (see search_total).

        `k` is clamped to MAX_K: a page is a page, and a request for a million
        documents is a request for the whole corpus with the bodies attached."""
        offset, k = max(0, offset), max(1, min(k, MAX_K))
        if query.strip():
            log_search_once(query.strip())
            rows = hybrid_search(query, k, offset)
        else:
            # No query: most-recently-updated, paged the same way.
            rows = q(DOC_SQL.format(where="WHERE d.project_id = %s") + " LIMIT %s OFFSET %s",
                     (access.require_current_access().project_id, k, offset))
        return [_doc(r) for r in rows]

    @strawberry.field
    def search_total(self, query: str = "") -> int:
        """How many documents `search` would return for this query with no
        limit. The count the results feed puts above the list."""
        if query.strip():
            return hybrid_count(query)
        return int(q1("SELECT count(*) AS n FROM documents WHERE project_id = %s",
                      (access.require_current_access().project_id,))["n"])

    @strawberry.field
    def recent_searches(self, limit: int = 6) -> list[str]:
        """The queries this workspace has actually run, newest distinct first.
        Read from usage_log, which the search resolver already writes — nothing
        is recorded here that a person did not type."""
        return [r["detail"] for r in q(
            """SELECT detail, max(at) AS last FROM usage_log
               WHERE kind = 'search' AND detail <> ''
               GROUP BY detail ORDER BY last DESC LIMIT %s""", (max(1, limit),))]

    @strawberry.field
    def related_documents(self, document_id: int) -> list[RelatedDoc]:
        """Documents one lineage edge away, in both directions. Real `edges`
        rows only: a document nothing links to answers [], which is the truth
        about it rather than an empty section with no explanation."""
        # The inbound half (`e.to_doc = %s`) had no index to sit on until
        # edges_to_doc_idx (STATS-5) — the unique index leads with from_doc, so
        # this read the whole edge table to answer "what links here". Ordering
        # ends in id so two documents with the same title do not swap places
        # between reads.
        project_id = access.require_current_access().project_id
        rows = q("""
          SELECT d.id, d.source, d.title, e.rel, 'out' AS direction
          FROM edges e JOIN documents d ON d.id = e.to_doc
          WHERE e.project_id = %s AND d.project_id = %s AND e.from_doc = %s
          UNION ALL
          SELECT d.id, d.source, d.title, e.rel, 'in' AS direction
          FROM edges e JOIN documents d ON d.id = e.from_doc
          WHERE e.project_id = %s AND d.project_id = %s AND e.to_doc = %s
          ORDER BY title, id, rel""", (project_id, project_id, document_id,
                                        project_id, project_id, document_id))
        return [RelatedDoc(id=r["id"], source=r["source"], title=r["title"],
                           rel=r["rel"], direction=r["direction"]) for r in rows]

    @strawberry.field
    def document(self, id: int) -> Document | None:
        project_id = access.require_current_access().project_id
        rows = q(DOC_SQL.format(where="WHERE d.project_id = %s AND d.id = %s"), (project_id, id))
        if not rows:
            return None
        # Per-user, and it must be the same name toggleWatch writes, or the
        # star never comes back lit for the person who set it.
        watched = q1("SELECT 1 AS x FROM watches WHERE user_name = %s AND document_id = %s",
                     (actor_name(), id)) is not None
        return _doc(rows[0], watched)

    @strawberry.field
    def revisions(self, document_id: int) -> list[AuditEvent]:
        project_id = access.require_current_access().project_id
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in q("""SELECT e.* FROM events e JOIN documents d ON e.target = d.title
                              WHERE d.project_id = %s AND e.project_id = %s AND d.id = %s
                              ORDER BY e.occurred_at DESC LIMIT 20""",
                            (project_id, project_id, document_id))]

    @strawberry.field
    def findings(self, document_id: int) -> list[Finding]:
        project_id = access.require_current_access().project_id
        return [Finding(id=r["id"], kind=r["kind"], severity=r["severity"], text=r["text"], note=r["note"])
                for r in q("""SELECT f.* FROM findings f JOIN documents d ON d.id = f.document_id
                              WHERE d.project_id = %s AND f.document_id = %s ORDER BY f.id""",
                           (project_id, document_id))]

    @strawberry.field
    def changes(self, document_id: int) -> list[Change]:
        project_id = access.require_current_access().project_id
        return [Change(id=r["id"], original=r["original"], replacement=r["replacement"],
                       reason=r["reason"], status=r["status"])
                for r in q("""SELECT c.* FROM changes c JOIN documents d ON d.id = c.document_id
                              WHERE d.project_id = %s AND c.document_id = %s ORDER BY c.id""",
                           (project_id, document_id))]

    @strawberry.field
    def lineage(self) -> list[LineageNode]:
        project_id = access.require_current_access().project_id
        # The two degree counts used to be correlated subqueries evaluated once
        # per document — with no index on edges(to_doc), that is O(documents ×
        # edges) and it grew quadratically with the graph the page exists to
        # draw (STATS-5). `degree` aggregates the edge table ONCE, in a single
        # pass over both endpoints, and the result is joined in. Documents with
        # no edges are absent from `degree` and coalesce to 0, which is the same
        # answer count(*) gave them.
        rows = q("""
          WITH degree AS (
            SELECT doc, sum(inb) AS inbound, sum(outb) AS outbound FROM (
              SELECT to_doc   AS doc, 1 AS inb, 0 AS outb FROM edges WHERE project_id = %s
              UNION ALL
              SELECT from_doc AS doc, 0 AS inb, 1 AS outb FROM edges WHERE project_id = %s
            ) x GROUP BY doc
          )
          SELECT d.id, d.source, d.external_id, d.title, d.author, d.updated_src, d.created_src,
                 d.graph_x, d.graph_y, d.graph_icon, d.graph_meta,
                 d.kind, d.source_path, d.source_id, s.kind AS src_kind,
                 s.config->>'repo' AS repo,
                 array_remove(array_agg(DISTINCT t.tag), NULL) AS tags,
                 coalesce(max(g.inbound), 0)::int AS inbound,
                 coalesce(max(g.outbound), 0)::int AS outbound
          FROM documents d
          LEFT JOIN tags t ON t.project_id = d.project_id AND t.document_id = d.id
          LEFT JOIN sources s ON s.project_id = d.project_id AND s.id = d.source_id
          LEFT JOIN degree g ON g.doc = d.id
          WHERE d.project_id = %s
          GROUP BY d.id, s.kind, s.config ORDER BY d.id""",
                 (project_id, project_id, project_id))
        pos = layout_nodes(rows)
        today = dt.date.today()
        out = []
        for r in rows:
            x, y = pos[r["id"]]
            doc_kind, group = classify_node(r)
            tags = r["tags"] or []
            updated, created = r["updated_src"], r["created_src"] or r["updated_src"]
            out.append(LineageNode(
                id=r["external_id"], doc_id=r["id"], source=r["source"], title=r["title"],
                meta=r["graph_meta"] or f"{r['author']} · {updated.strftime('%b %-d, %Y') if updated else '—'}",
                icon=r["graph_icon"] or SOURCE_ICONS.get(r["source"], "doc"),
                x=round(x, 4), y=round(y, 4), pinned=r["graph_x"] is not None,
                date=updated.isoformat() if updated else "",
                created_date=created.isoformat() if created else "",
                warn=bool({"stale", "needs-review"} & set(tags)),
                owner=r["author"], tags=tags,
                stale_days=(today - updated).days if updated else 0,
                orphan=r["inbound"] + r["outbound"] == 0,
                inbound=r["inbound"], outbound=r["outbound"],
                doc_kind=doc_kind, group=group))
        return out

    @strawberry.field
    def lineage_edges(self) -> list[LineageEdge]:
        project_id = access.require_current_access().project_id
        rows = q("""
          SELECT e.id, f.external_id AS from_id, t.external_id AS to_id, e.rel, e.created_at,
                 e.curve, e.meta
          FROM edges e JOIN documents f ON f.id = e.from_doc JOIN documents t ON t.id = e.to_doc
          WHERE e.project_id = %s AND f.project_id = %s AND t.project_id = %s
          ORDER BY e.id""", (project_id, project_id, project_id))
        return [LineageEdge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], kind=r["rel"],
                            date=r["created_at"].isoformat() if r["created_at"] else "",
                            curve=r["curve"], meta=jload(r["meta"])) for r in rows]

    @strawberry.field
    def graph_stats(self) -> GraphStats:
        project_id = access.require_current_access().project_id
        # "stale" is relative to the newest doc so seeded 2024 data reads sensibly.
        s = q1("""
          SELECT count(*) AS docs,
                 count(*) FILTER (WHERE d.updated_src < (SELECT max(updated_src) FROM documents) - 45
                                  OR EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id
                                             AND t.tag IN ('stale','needs-review'))) AS stale,
                 count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM edges e WHERE e.from_doc = d.id OR e.to_doc = d.id)) AS orphans,
                 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id
                                                AND t.tag = 'customer-facing')
                                  AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.rel = 'translates'
                                                  AND (e.from_doc = d.id OR e.to_doc = d.id))) AS untranslated,
                 count(*) FILTER (WHERE d.author = '' OR d.author = 'CI') AS unowned
          FROM documents d WHERE d.project_id = %s""", (project_id,))
        contradictions = q1("""SELECT count(*) AS n FROM edges
                                WHERE project_id = %s AND rel = 'contradicts'""", (project_id,))["n"]
        cited = q("""SELECT d.title, d.id, count(*) AS inbound FROM edges e
                     JOIN documents d ON d.id = e.to_doc
                     WHERE e.project_id = %s AND d.project_id = %s
                     GROUP BY d.id ORDER BY inbound DESC, d.id LIMIT 3""", (project_id, project_id))
        activity = q("""SELECT * FROM (
                          SELECT occurred_at::date AS day, count(*) AS n FROM events
                          WHERE project_id = %s
                          GROUP BY 1 ORDER BY 1 DESC LIMIT 60) x ORDER BY day""", (project_id,))
        return GraphStats(
            docs=s["docs"], stale=s["stale"], orphans=s["orphans"],
            untranslated=s["untranslated"], unowned=s["unowned"], contradictions=int(contradictions),
            top_cited=[TopCited(title=r["title"], doc_id=r["id"], inbound=r["inbound"]) for r in cited],
            activity=[ActivityBucket(date=r["day"].isoformat(), count=r["n"]) for r in activity])

    @strawberry.field
    def doc_history(self, document_id: int) -> list[DocHistory]:
        return [DocHistory(at=r["at"], actor=r["actor"], verb=r["verb"], detail=r["target"])
                for r in q("""SELECT e.actor, e.verb, e.target,
                                     to_char(e.occurred_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS at
                              FROM events e JOIN documents d ON e.target = d.title
                              WHERE d.id = %s ORDER BY e.occurred_at DESC LIMIT 30""", (document_id,))]

    @strawberry.field
    def graph_views(self) -> list[GraphView]:
        project_id = access.require_current_access().project_id
        return [GraphView(id=r["id"], name=r["name"], state=json.dumps(jload(r["state"])))
                for r in q("""SELECT id, name, state FROM graph_views
                            WHERE project_id = %s ORDER BY id""", (project_id,))]

    @strawberry.field
    def facts(self) -> list[Fact]:
        # verified is facts.verified_at (a DATE) as ISO — not the legacy
        # facts.verified display string, which the console cannot re-format
        # and whose '—' placeholder parses as an invalid date.
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"],
                     verified=r["verified_at"].isoformat() if r["verified_at"] else "")
                for r in q("SELECT * FROM facts WHERE project_id = %s ORDER BY id",
                           (access.require_current_access().project_id,))]

    @strawberry.field
    def claims(self, document_id: int) -> list[Fact]:
        """The claims mined from ONE document, by key. `facts.document_id` is
        written by the extractor at the moment it reads a document, so this is
        provenance the server recorded, never provenance inferred from the
        free-text `facts.source` label. Facts landed before the column existed
        (and hand-written ones with no citation) carry NULL and belong to no
        document — they are absent here rather than attributed to a guess."""
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"],
                     verified=r["verified_at"].isoformat() if r["verified_at"] else "")
                for r in q("SELECT * FROM facts WHERE project_id = %s AND document_id = %s ORDER BY id",
                           (access.require_current_access().project_id, document_id))]

    @strawberry.field
    def fact_contradictions(self) -> list[FactContradiction]:
        """Claims in the ledger that disagree with each other. Deterministic
        (see detect_contradictions) — no model call, and [] on a ledger whose
        claims are consistent, which is the common case."""
        rows = q("SELECT id, claim, status FROM facts WHERE project_id = %s ORDER BY id",
                 (access.require_current_access().project_id,))
        return [FactContradiction(
            fact_id=a["id"], claim=a["claim"], status=a["status"],
            other_fact_id=b["id"], other_claim=b["claim"], other_status=b["status"],
            reason=reason, detail=detail)
            for a, b, reason, detail in detect_contradictions(rows)]

    @strawberry.field
    def tasks(self) -> list[Task]:
        # overdue is derived in SQL against the database's own current_date, so
        # it cannot drift from the due date the same row reports.
        rows = q("""SELECT *, (due_date IS NOT NULL AND NOT done AND due_date < current_date) AS overdue
                    FROM tasks WHERE project_id = %s ORDER BY id""",
                 (access.require_current_access().project_id,))
        return [Task(id=r["id"], title=r["title"], assignee_initials=r["assignee_initials"],
                     assignee_tint=r["assignee_tint"], kind=r["kind"], kind_label=r["kind_label"],
                     done=r["done"], due=r["due_date"].isoformat() if r["due_date"] else "",
                     overdue=bool(r["overdue"]), subject_type=r["subject_type"],
                     subject_id=r["subject_id"], subject_title=r["subject_title"],
                     subject_href=r["subject_href"])
                for r in rows]

    @strawberry.field
    def review_items(self, first: int = 50, after: str = "", kinds: list[str] | None = None,
                     statuses: list[str] | None = None, sources: list[str] | None = None,
                     assignees: list[str] | None = None, due: str = "") -> ReviewConnection:
        """A bounded, stable projection over every product object requiring review."""
        limit = min(max(first, 1), 100)
        offset = review_application.decode_cursor(after)
        filtered = review_application.filter_items(review_repository.project_items(), kinds=kinds, statuses=statuses,
                                       sources=sources, assignees=assignees, due=due)
        page = filtered[offset:offset + limit]
        return ReviewConnection(
            items=[ReviewItem(
                id=x.id, kind=x.kind, title=x.title, status=x.status, source=x.source,
                assignee=x.assignee, due=x.due, subject_type=x.subject_type,
                subject_id=x.subject_id, subject_title=x.subject_title,
                subject_href=x.subject_href, confidence=x.confidence,
                evidence_count=x.evidence_count, trusted_source=x.trusted_source,
            ) for x in page],
            total_count=len(filtered),
            page_info=ReviewPageInfo(
                end_cursor=review_application.encode_cursor(offset + len(page)),
                has_next_page=offset + len(page) < len(filtered)),
        )

    @strawberry.field
    def tasks_summary(self) -> TaskSummary | None:
        """The strip above the board: counts and rosters rolled up off the same
        rows `tasks` returns. None on an empty inbox — there is nothing to
        summarise, and the board renders without a headline."""
        project_id = access.require_current_access().project_id
        s = q1("""SELECT count(*) AS total,
                         count(*) FILTER (WHERE NOT done) AS open,
                         count(*) FILTER (WHERE done) AS done,
                         count(*) FILTER (WHERE NOT done AND due_date < current_date) AS overdue,
                         count(*) FILTER (WHERE NOT done AND due_date >= current_date
                                          AND due_date <= current_date + 7) AS due_soon
                  FROM tasks WHERE project_id = %s""", (project_id,))
        if not s or not s["total"]:
            return None
        tags = [r["kind_label"] for r in q(
            """SELECT DISTINCT kind_label FROM tasks
               WHERE project_id = %s AND kind_label <> '' ORDER BY kind_label""", (project_id,))]
        people = [r["assignee_initials"] for r in q(
            """SELECT DISTINCT assignee_initials FROM tasks
               WHERE project_id = %s AND NOT done AND assignee_initials <> ''
               ORDER BY assignee_initials""", (project_id,))]
        # The headline is whichever number needs acting on: a missed deadline
        # outranks the open count, and "all caught up" is itself the news.
        if s["overdue"]:
            value, label = str(s["overdue"]), "overdue"
        elif s["open"]:
            value, label = str(s["open"]), "open"
        else:
            value, label = str(s["done"]), "done"
        return TaskSummary(
            title="Review queue", tags=tags, people=people,
            stat_value=value, stat_label=label,
            open_count=int(s["open"]), done_count=int(s["done"]),
            overdue_count=int(s["overdue"]), due_soon_count=int(s["due_soon"]))

    # `askMari` used to live here, reading the last row of `ask_answers`. That
    # table has no writer anywhere in the tree, so the field raised IndexError
    # on every install, and nothing in the console queried it: the live
    # question-and-answer surface is `approvedAnswers` (app.py /chat serves it,
    # mutations_knowledge writes it). A field that can only throw is worse than
    # no field, so it is gone. Left for whoever owns those files:
    # gqltypes.AskMari/AskSource and init.sql's `ask_answers` table are now
    # unreferenced and should go with it.

    @strawberry.field
    def digest(self) -> list[DigestTopic]:
        project_id = access.require_current_access().project_id
        return [DigestTopic(title=r["title"], summary=r["summary"],
                            where=[DigestWhere(**w) for w in jload(r["wheres"])],
                            impact=[DigestImpact(**i) for i in jload(r["impact"])])
                for r in q("SELECT * FROM digest_topics WHERE project_id = %s ORDER BY id",
                           (project_id,))]

    @strawberry.field
    def members(self) -> list[Member]:
        return [Member(id=r["id"], name=r["name"], initials=r["initials"], tint=r["tint"],
                       email=r["email"], role=r["role"], provider=r["provider"], status=r["status"],
                       joined=r["joined"].isoformat())
                for r in q("SELECT * FROM users ORDER BY id")]

    @strawberry.field
    def glossary(self) -> list[GlossaryTerm]:
        return [GlossaryTerm(id=r["id"], term=r["term"], definition=r["definition"],
                             owner=r["owner_name"], updated=r["updated"].isoformat())
                for r in q("SELECT * FROM glossary WHERE project_id = %s ORDER BY term",
                           (access.require_current_access().project_id,))]

    @strawberry.field
    def tag_defs(self) -> list[TagDef]:
        # usage is a real count off the tags table — the Library's tag panel
        # reads "used on N of M documents", so a definition nobody has applied
        # must come back as 0 rather than as a blank the UI can round up.
        return [TagDef(tag=r["tag"], label=r["label"], kind=r["kind"], search_weight=r["search_weight"],
                       is_default=r["is_default"], behaviors=r["behaviors"], usage=int(r["usage"]))
                for r in q("""SELECT d.*, (SELECT count(*) FROM tags t WHERE t.tag = d.tag) AS usage
                              FROM tag_definitions d ORDER BY d.search_weight DESC""")]

    # ——— editorial system: style guides, rule registry, templates, voice ———

    @strawberry.field
    def style_guides(self) -> list[StyleGuide]:
        """The style packs this workspace can adopt. `rules` and `preview` are
        read off `style_rules` in one pass, so a pack's advertised rule count
        is literally the rules it has. A workspace that deleted every shipped
        pack gets [] and the guides tab renders its own empty state."""
        rules: dict[str, list[str]] = {}
        for r in q("SELECT guide_key, description FROM style_rules ORDER BY guide_key, sort, id"):
            rules.setdefault(r["guide_key"], []).append(r["description"])
        return [StyleGuide(key=g["key"], name=g["name"], description=g["description"],
                           tone=g["tone"], builtin=g["builtin"],
                           rules=len(rules.get(g["key"], [])), preview=rules.get(g["key"], []))
                for g in q("SELECT * FROM style_guides ORDER BY sort, key")]

    @strawberry.field
    def style_rules(self, guide_key: str | None = None) -> list[StyleRule]:
        """The deterministic rule registry, optionally one pack's. The Library's
        rules tab counts this list, so the number on the tab strip is the number
        of rules a document is actually checked against."""
        where = "WHERE guide_key = %s" if guide_key else ""
        return [StyleRule(id=r["id"], guide_key=r["guide_key"], family=r["family"],
                          severity=r["severity"], description=r["description"],
                          pack=r["pack"], suggestion=r["suggestion"])
                for r in q(f"SELECT * FROM style_rules {where} ORDER BY guide_key, sort, id",
                           (guide_key,) if guide_key else ())]

    @strawberry.field
    def default_style_pack(self) -> str:
        """The pack this workspace adopted (`style_guide.default_pack`), or ''
        when nobody has chosen one — a real state on a fresh install."""
        row = q1("SELECT value FROM settings WHERE key = 'style_guide'")
        v = jload(row["value"]) if row else {}
        return str(v.get("default_pack") or "") if isinstance(v, dict) else ""

    @strawberry.field
    def voice_layer(self) -> VoiceLayer:
        """The workspace's voice layer (`settings.voice`). Blank and all-off on
        a workspace that has not written one down, which the panel renders as
        empty fields rather than as someone else's voice."""
        row = q1("SELECT value FROM settings WHERE key = 'voice'")
        v = jload(row["value"]) if row else {}
        if not isinstance(v, dict):
            v = {}
        return VoiceLayer(voice=str(v.get("voice") or ""), terms=str(v.get("terms") or ""),
                          banned=str(v.get("banned") or ""), inclusive=bool(v.get("inclusive")),
                          jargon=bool(v.get("jargon")), sentence_case=bool(v.get("sentence_case")))

    @strawberry.field
    def document_templates(self) -> list[DocumentTemplate]:
        """Scaffolds a new document can start from. `standard` separates the
        shipped set from templates this workspace wrote."""
        return [DocumentTemplate(key=r["key"], name=r["name"], category=r["category"],
                                 description=r["description"],
                                 sections=[str(s) for s in (jload(r["sections"]) or [])],
                                 icon=r["icon"], standard=r["standard"])
                for r in q("SELECT * FROM document_templates ORDER BY sort, key")]

    @strawberry.field
    def upload_manifest(self) -> UploadManifest:
        """What the Upload connector ingested, per file: chunks written and how
        many carry a vector. Counted off `chunks` at read time. A workspace that
        has uploaded nothing gets an empty manifest and a '' summary — never a
        sample receipt."""
        project_id = access.require_current_access().project_id
        rows = q("""SELECT d.id, d.source_path, d.external_id, d.updated_src,
                           count(c.id) AS chunks,
                           count(c.embedding) AS embedded
                    FROM documents d
                    JOIN sources s ON s.project_id = d.project_id AND s.id = d.source_id
                                      AND s.provider = 'upload'
                    LEFT JOIN chunks c ON c.project_id = d.project_id AND c.document_id = d.id
                    WHERE d.project_id = %s
                    GROUP BY d.id ORDER BY d.id""", (project_id,))
        files = []
        for r in rows:
            # source_path is 'upload/<file>' and external_id 'upload:<file>';
            # the row shows the file name the person actually dropped in.
            name = (r["source_path"] or "").split("/", 1)[-1] or r["external_id"].split(":", 1)[-1]
            chunks, embedded = int(r["chunks"]), int(r["embedded"])
            files.append(UploadFile(
                name=name, doc_id=r["id"], chunks=chunks, embedded=embedded,
                detail=f"{chunks} chunk{'' if chunks == 1 else 's'} · {embedded} embedded",
                ingested_at=r["updated_src"].isoformat() if r["updated_src"] else ""))
        total_chunks = sum(f.chunks for f in files)
        total_embedded = sum(f.embedded for f in files)
        summary = (f"{len(files)} file{'' if len(files) == 1 else 's'} · "
                   f"{total_chunks} chunks · {total_embedded} embedded") if files else ""
        return UploadManifest(files=files, file_count=len(files), chunk_count=total_chunks,
                              embedded_count=total_embedded, summary=summary)

    @strawberry.field
    def api_keys(self) -> list[ApiKey]:
        project_id = access.require_current_access().project_id
        return [ApiKey(id=r["id"], name=r["name"], prefix=r["prefix"], scopes=r["scopes"],
                       created=r["created_at"].isoformat(), last_used=r["last_used"], revoked=r["revoked"])
                for r in q("SELECT * FROM api_keys WHERE project_id = %s ORDER BY id", (project_id,))]

    @strawberry.field
    def mcp_servers(self) -> list[McpServer]:
        # token is shown once at creation (createMcpServer) — never re-served here.
        return [McpServer(id=r["id"], name=r["name"], url=r["url"], scope=r["scope"],
                          status=r["status"], tools=r["tools"], config=jload(r["config"]),
                          token="")
                for r in q("SELECT * FROM mcp_servers WHERE project_id = %s ORDER BY id",
                           (access.require_current_access().project_id,))]

    @strawberry.field
    def checkpoints(self) -> list[Checkpoint]:
        return [Checkpoint(id=r["id"], provider=r["provider"], item=r["item"], stage=r["stage"],
                           progress=r["progress"], total=r["total"], cursor_id=r["cursor_id"],
                           duration=r["duration"], status=r["status"])
                for r in q("SELECT * FROM ingest_checkpoints ORDER BY id")]

    @strawberry.field
    def sync_events(self) -> list[SyncEvent]:
        return [SyncEvent(id=r["id"], provider=r["provider"], event=r["event"], detail=r["detail"], at=r["at_label"])
                for r in q("SELECT * FROM sync_events ORDER BY id DESC LIMIT 12")]

    @strawberry.field
    def audit_log(self, limit: int = 40, query: str = "",
                  date_from: str | None = None, date_to: str | None = None) -> list[AuditEvent]:
        """`query` matches actor, verb or target; `dateFrom`/`dateTo` are ISO
        dates bounding `occurred_at` inclusively. The access log is deeper than
        any window the console can hold, so the filter has to reach the whole
        table rather than narrowing the page already fetched."""
        where, args = _audit_where(query, date_from, date_to)
        project_id = access.require_current_access().project_id
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in q(f"SELECT * FROM events WHERE project_id = %s AND {where} ORDER BY occurred_at DESC, id DESC LIMIT %s",
                           (project_id,) + args + (limit,))]

    @strawberry.field
    def audit_log_total(self, query: str = "",
                        date_from: str | None = None, date_to: str | None = None) -> int:
        """Rows matching the same filter, so the console can say what its window
        is a window onto instead of comparing a filtered view against the
        window's own size. No filter = the whole log."""
        where, args = _audit_where(query, date_from, date_to)
        return int(q1(f"SELECT count(*) AS n FROM events WHERE project_id = %s AND {where}",
                      (access.require_current_access().project_id,) + args)["n"])

    @strawberry.field
    def activity_feed(self, limit: int = 12) -> list[ActivityItem]:
        """Live feed for the overview: runs, edits, deploys — not chatter."""
        rows = q("""
          SELECT id, actor, verb, target,
                 to_char(occurred_at, 'HH24:MI') AS at,
                 -- Age in seconds. The console's live feed counts up from this
                 -- between polls ("42s ago" → "1m ago"), which a wall-clock
                 -- "HH:MI" cannot support: it has no date, so an event from
                 -- yesterday renders as if it just happened.
                 greatest(0, extract(epoch FROM now() - occurred_at))::int AS seconds_ago,
                 CASE
                   WHEN verb LIKE '%%run%%' OR verb LIKE 'started%%' THEN 'run'
                   WHEN verb LIKE 'deploy%%' OR verb LIKE 'rolled%%' THEN 'deploy'
                   WHEN verb LIKE '%%fact%%' OR verb LIKE '%%verif%%' THEN 'fact'
                   WHEN verb LIKE '%%task%%' OR verb IN ('completed','reopened') THEN 'task'
                   WHEN verb LIKE '%%sync%%' OR verb LIKE '%%connect%%' THEN 'sync'
                   WHEN verb LIKE '%%link%%' OR verb LIKE 'derived%%' THEN 'link'
                   ELSE 'edit'
                 END AS kind
          FROM events WHERE project_id = %s ORDER BY occurred_at DESC, id DESC LIMIT %s
        """, (access.require_current_access().project_id, limit))
        return [ActivityItem(id=r["id"], kind=r["kind"], actor=r["actor"],
                             text=r["verb"], target=r["target"], at=r["at"],
                             seconds_ago=int(r["seconds_ago"])) for r in rows]

    @strawberry.field
    def workflows(self) -> list[Workflow]:
        return [Workflow(id=r["id"], name=r["name"], description=r["description"], color=r["color"],
                         pinned=r["pinned"], status=r["status"], nodes=jload(r["nodes"]),
                         trigger=jload(r.get("trigger")) or {})
                for r in q("SELECT * FROM workflows ORDER BY id")]

    @strawberry.field
    def workflow_runs(self, workflow_id: int | None = None) -> list[WorkflowRun]:
        where = "WHERE r.workflow_id = %s" if workflow_id else ""
        rows = q(f"""SELECT r.*, w.name AS wf_name FROM workflow_runs r
                     JOIN workflows w ON w.id = r.workflow_id {where}
                     ORDER BY r.number DESC LIMIT 10""",
                 (workflow_id,) if workflow_id else ())
        return [WorkflowRun(id=r["id"], workflow_id=r["workflow_id"], workflow_name=r["wf_name"],
                            number=r["number"], status=r["status"],
                            # ISO, not started_label: the console formats and
                            # sorts on this (see _doc's date for the same rule).
                            started=r["started_at"].isoformat() if r.get("started_at") else "",
                            duration=r["duration"], progress=r["progress"],
                            stats=jload(r["stats"]), rows=jload(r["rows_data"]),
                            triggered_by=r.get("triggered_by") or "") for r in rows]

    @strawberry.field
    def workflow_run(self, id: int) -> WorkflowRun | None:
        """One run by id. `workflowRuns` only returns the ten newest of a flow,
        which is not a way to follow a specific run someone just started."""
        rows = q("""SELECT r.*, w.name AS wf_name FROM workflow_runs r
                    JOIN workflows w ON w.id = r.workflow_id WHERE r.id = %s""", (id,))
        if not rows:
            return None
        r = rows[0]
        return WorkflowRun(id=r["id"], workflow_id=r["workflow_id"], workflow_name=r["wf_name"],
                           number=r["number"], status=r["status"],
                           started=r["started_at"].isoformat() if r.get("started_at") else "",
                           duration=r["duration"], progress=r["progress"],
                           stats=jload(r["stats"]), rows=jload(r["rows_data"]),
                           triggered_by=r.get("triggered_by") or "")

    @strawberry.field
    def knowledge_chat_destinations(self) -> list[KnowledgeChatDestination]:
        project_id = access.require_current_access().project_id
        project = q1("SELECT slug FROM projects WHERE id = %s", (project_id,)) or {"slug": ""}
        return [KnowledgeChatDestination(
                    id=r["id"], name=r["name"], slug=r["slug"], title=r["title"],
                    welcome=r["welcome"], status=r["status"],
                    url=f"/knowledge-chat/{project['slug']}/{r['slug']}")
                for r in q("""SELECT id, name, slug, title, welcome, status
                             FROM knowledge_chat_destinations
                             WHERE project_id = %s ORDER BY id""", (project_id,))]

    @strawberry.field
    def notifications(self) -> list[Notification]:
        """Only the caller's notifications — markNotificationsRead marks rows
        for `actor_name()`, so reading by anything else leaves the badge stuck."""
        return [Notification(id=r["id"], kind=r["kind"], text=r["text"], detail=r["detail"],
                             at=r["at_label"], read=r["read"])
                for r in q("SELECT * FROM notifications WHERE user_name = %s ORDER BY id",
                           (actor_name(),))]

    @strawberry.field
    def workspace(self) -> Workspace:
        """The `workspace` settings row, which first-run setup writes and
        Settings → Members edits. Fields nobody has filled in come back "" —
        an unnamed workspace is a real state, not a missing fetch."""
        row = q1("SELECT value FROM settings WHERE key = 'workspace'")
        v = jload(row["value"]) if row else {}
        if not isinstance(v, dict):
            v = {}
        return Workspace(name=str(v.get("name") or ""), slug=str(v.get("slug") or ""),
                         plan=str(v.get("plan") or ""), timezone=str(v.get("timezone") or ""),
                         language=str(v.get("language") or ""))

    @strawberry.field
    def provisioning(self) -> Provisioning:
        """Member provisioning as it is actually configured. Manual invites are
        always on (inviteMember is the only path the server implements); the
        GitHub team is whatever an admin saved, and counts as connected only
        when the server also holds a credential to read it with; SCIM has no
        endpoint, so it reports unavailable rather than "Enterprise"."""
        row = q1("SELECT value FROM settings WHERE key = 'provisioning'")
        v = jload(row["value"]) if row else {}
        if not isinstance(v, dict):
            v = {}
        team = str(v.get("github_team") or "")
        has_token = bool(github.configured_token())
        synced = int(q1("SELECT count(*) AS n FROM users WHERE provider = 'github'")["n"])
        providers = [name for name, key in (("github", "github_client_id"),
                                            ("google", "google_client_id"))
                     if config.get("auth", key)]
        return Provisioning(
            manual_invites=bool(v.get("manual_invites", True)),
            github_team=GithubTeamSync(team=team, connected=bool(team and has_token),
                                       credential=has_token, synced_members=synced),
            sso_providers=providers, sso_enabled=bool(providers),
            scim_enabled=bool(v.get("scim_enabled", False)),
            scim_status="unavailable")

    @strawberry.field
    def settings(self) -> list[Setting]:
        return [Setting(key=r["key"], value=_mask_setting(
                    r["key"], _effective_model_setting(r["key"], jload(r["value"]))))
                for r in q("SELECT * FROM settings ORDER BY key")]

    @strawberry.field
    def approved_answers(self) -> list[ApprovedAnswer]:
        return [ApprovedAnswer(id=r["id"], question=r["question"], answer=r["answer"], status=r["status"],
                               owner=r["owner_name"], channels=r["channels"], sources=jload(r["sources"]),
                               served=r["served"], spark=r["spark"],
                               updated=r["updated"].isoformat())
                for r in q("""SELECT * FROM approved_answers WHERE project_id = %s
                              ORDER BY (status = 'approved') DESC, served DESC""",
                           (access.require_current_access().project_id,))]

    @strawberry.field
    def answer_coverage_gaps(self, limit: int = 8) -> list[str]:
        """Questions people actually asked that no approved answer covers.

        Real demand only: search queries logged in usage_log plus questions put
        to the assistant. A question already served by an approved answer is
        not a gap, so anything matching one verbatim is filtered out. Returns
        [] on a workspace nobody has asked anything in — never a sample list."""
        rows = q("""
          WITH asked AS (
            SELECT lower(trim(detail)) AS question, max(at) AS last_at FROM usage_log
             WHERE kind = 'search' AND length(trim(detail)) >= 8 GROUP BY 1
            UNION ALL
            SELECT lower(trim(content)), max(created_at) FROM chat_messages
             WHERE role = 'user' AND length(trim(content)) BETWEEN 8 AND 200 GROUP BY 1)
          SELECT a.question, max(a.last_at) AS last_at FROM asked a
           WHERE NOT EXISTS (SELECT 1 FROM approved_answers ans
                              WHERE ans.status = 'approved' AND lower(ans.question) = a.question)
           GROUP BY a.question ORDER BY max(a.last_at) DESC LIMIT %s""", (limit,))
        return [r["question"] for r in rows]

    @strawberry.field
    def answer_harvest_sources(self) -> JSON:
        """Real provider-backed inputs available to answer harvesting."""
        from mari_components.connectors import CONNECTOR_CATALOG

        counts = {str(row["source"]): int(row["n"]) for row in q(
            "SELECT source, count(*) AS n FROM documents GROUP BY source"
        ) if row.get("source")}
        rows = [
            {"key": key, "label": definition.name, "count": counts[key]}
            for key, definition in CONNECTOR_CATALOG.items()
            if counts.get(key, 0) > 0
        ]
        chat = int(q1("SELECT count(*) AS n FROM chat_messages WHERE role = 'user'")["n"])
        if chat:
            rows.append({"key": "chat", "label": "Chat history", "count": chat})
        return rows

    @strawberry.field
    def index_stats(self) -> JSON:
        """Corpus size as the embedding config page states it: documents, the
        chunks they were split into, and how many of those carry a vector."""
        s = q1("""SELECT (SELECT count(*) FROM documents) AS docs,
                         count(*) AS chunks,
                         count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded
                  FROM chunks""") or {"docs": 0, "chunks": 0, "embedded": 0}
        return {"docs": int(s["docs"]), "chunks": int(s["chunks"]), "embedded": int(s["embedded"])}

    @strawberry.field
    def model_catalog(self) -> JSON:
        """Provider-reported model choices; never a hardcoded UI catalog."""
        return llm.model_catalog()

    @strawberry.field
    def bots_status(self) -> JSON:
        """Slack + GitHub bot wiring, as GET /bots/status reports it. Same
        function, so the console's Sources page and the REST surface can never
        disagree about whether a bot is configured."""
        from mari_server.api import bots
        return bots.bots_status(access.require_current_access())

    @strawberry.field
    def connector_catalog(self) -> JSON:
        """The connector catalog, as GET /connectors/catalog reports it: field
        SPECS only, never stored values (CONNECTORS-CONTRACT.md)."""
        from mari_server.api import connectors as connectors_api
        return connectors_api.catalog()

    @strawberry.field
    def decisions(self) -> list[Decision]:
        project_id = access.require_current_access().project_id
        rows = q("""SELECT d.*, s.statement AS sup_stmt FROM decisions d
                    LEFT JOIN decisions s ON s.project_id = d.project_id AND s.id = d.superseded_by
                    WHERE d.project_id = %s ORDER BY d.id DESC""", (project_id,))
        return [Decision(id=r["id"], statement=r["statement"], context=r["context"], status=r["status"],
                         source_label=r["source_label"], owners=r["owners"],
                         decided_on=r["decided_on"].isoformat() if r["decided_on"] else "",
                         superseded_by=r["superseded_by"], superseded_by_statement=r["sup_stmt"] or "",
                         impact_summary=r["impact_summary"], impact_count=r["impact_count"]) for r in rows]

    @strawberry.field
    def insight_stats(self, since: str | None = None, until: str | None = None) -> InsightStats:
        """Every number is a real count (BOTS-CONTRACT.md §A) — never a constant.

        `since`/`until` are ISO dates bounding the window the dashboard's range
        picker is on. ALL FOUR counts move with the window, so the sentence the
        page writes ("over the last 30 days, from …") describes every tile
        above it and not just the two that happen to have a timestamp."""
        floor, ceil = _iso_date_arg(since), _iso_date_arg(until)
        # One predicate, applied to each table's own timestamp column.
        def window(col: str) -> tuple[str, tuple]:
            clauses, args = [], []
            if floor:
                clauses.append(f"{col} >= %s")
                args.append(floor)
            if ceil:
                clauses.append(f"{col} < (%s::date + 1)")
                args.append(ceil)
            return (" AND ".join(clauses) if clauses else "true"), tuple(args)

        w_usage, a_usage = window("at")
        counts = q1(f"""
          SELECT count(*) FILTER (WHERE kind = 'search') AS searches,
                 count(*) FILTER (WHERE kind = 'chat_answer') AS served,
                 min(at) AS since
          FROM usage_log WHERE {w_usage}""", a_usage) or {"searches": 0, "served": 0, "since": None}
        w_found, a_found = window("created_at")
        drift = q1(f"SELECT count(*) AS n FROM findings WHERE kind IN ('fact','freshness') AND {w_found}",
                   a_found)["n"]
        w_chg, a_chg = window("created_at")
        fixed = q1(f"SELECT count(*) AS n FROM changes WHERE status = 'accepted' AND {w_chg}", a_chg)["n"]
        # The window's own floor when the caller named one; otherwise the first
        # thing this workspace ever did, which is what "since" meant before
        # there was a picker. "" when it has done nothing at all.
        start = floor or (counts["since"].isoformat() if counts["since"] else "")
        return InsightStats(searches=int(counts["searches"]), answers_served=int(counts["served"]),
                            drift_caught=int(drift), docs_fixed=int(fixed), since=start)

    @strawberry.field
    def freshness(self) -> list[FreshnessRow]:
        """Rollup over connected sources only (docs with a source_id) — one row
        per live sources row; seed/sample docs never masquerade as a provider."""
        project_id = access.require_current_access().project_id
        rows = q("""
          WITH bucketed AS (
            SELECT d.source_id,
                   CASE WHEN d.updated_src IS NULL OR d.updated_src < current_date - 30
                          OR EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id AND t.tag = 'stale')
                        THEN 'stale'
                        WHEN d.updated_src < current_date - 7 THEN 'aging'
                        ELSE 'fresh' END AS bucket
            FROM documents d WHERE d.project_id = %s AND d.source_id IS NOT NULL)
          SELECT s.display_name AS source, s.provider AS provider,
                 count(*) FILTER (WHERE b.bucket = 'fresh') AS fresh,
                 count(*) FILTER (WHERE b.bucket = 'aging') AS aging,
                 count(*) FILTER (WHERE b.bucket = 'stale') AS stale
          FROM sources s LEFT JOIN bucketed b ON b.source_id = s.id
          WHERE s.project_id = %s
          GROUP BY s.id, s.display_name, s.provider ORDER BY count(b.source_id) DESC, s.id""",
                 (project_id, project_id))
        return [FreshnessRow(source=r["source"], provider=r["provider"], fresh=r["fresh"],
                             aging=r["aging"], stale=r["stale"]) for r in rows]

    @strawberry.field
    def readability(self) -> list[ReadabilityRow]:
        rows = q("""SELECT id, title, source, readability FROM documents
                    WHERE project_id = %s ORDER BY id""", (access.require_current_access().project_id,))
        out = []
        for r in rows:
            grade, note = (r["readability"].split("|", 1) + [""])[:2] if r["readability"] else ("", "")
            out.append(ReadabilityRow(id=r["id"], title=r["title"], source=r["source"], grade=grade, note=note))
        return out

    @strawberry.field
    def glossary_candidates(self) -> list[GlossaryCandidate]:
        # evidence is the document the harvester found the term in — the
        # provenance the review step needs to judge a candidate. '' / 0 for a
        # term typed in by hand, which was never mined from a document.
        return [GlossaryCandidate(id=r["id"], term=r["term"], variants=r["variants"],
                                  definition=r["definition"], evidence=r["evidence"] or "",
                                  evidence_doc_id=r["evidence_doc_id"] or 0)
                for r in q("SELECT * FROM glossary WHERE project_id = %s AND candidate ORDER BY id",
                           (access.require_current_access().project_id,))]

    @strawberry.field
    def audit_runs(self) -> list[AuditRun]:
        return [AuditRun(id=r["id"], provider=r["provider"], repo=r["repo"], findings=r["findings"],
                         fixed=r["fixed"], ran_at=r["ran_at"].isoformat())
                for r in q("SELECT * FROM audit_runs ORDER BY id DESC LIMIT 10")]

    @strawberry.field
    def audit_findings(self, run_id: int | None = None) -> list[AuditFinding]:
        where = "WHERE run_id = %s" if run_id else "WHERE run_id = (SELECT max(id) FROM audit_runs)"
        return [AuditFinding(id=r["id"], run_id=r["run_id"], kind=r["kind"], title=r["title"],
                             detail=r["detail"], fix_action=r["fix_action"],
                             fix_payload=jload(r["fix_payload"]), status=r["status"])
                for r in q(f"SELECT * FROM audit_findings {where} ORDER BY kind, id",
                           (run_id,) if run_id else ())]

    @strawberry.field
    def chat_sessions(self, info: strawberry.Info) -> list[ChatSession]:
        ctx = access.require_current_access()
        user = info.context.get("user") or {}
        user_id = int(user.get("id") or 0)
        if not user_id:
            return []
        out = []
        for s in q("""SELECT * FROM chat_sessions
                       WHERE project_id = %s AND owner_user_id = %s
                       ORDER BY id DESC LIMIT 20""", (ctx.project_id, user_id)):
            msgs = q("""SELECT * FROM chat_messages
                        WHERE project_id = %s AND session_id = %s ORDER BY id""",
                     (ctx.project_id, s["id"]))
            out.append(ChatSession(id=s["id"], title=s["title"],
                                   messages=[ChatMessage(id=m["id"], role=m["role"], content=m["content"],
                                                         sources=jload(m["sources"])) for m in msgs]))
        return out

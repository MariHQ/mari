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

from mari_server import settings as config
from mari_server.identity import access
from mari_server.providers import github
from mari_server.sources import sync as ingest
from mari_server.providers import models as llm
from mari_server.providers import vectors as retrieval
from mari_components import review as review_application
from mari_server.persistence.postgres import review as review_repository
from mari_server.persistence.postgres import knowledge as knowledge_store
from mari_server.persistence.postgres import documents as document_repository, lineage as lineage_store
from mari_server.persistence.postgres import trajectories as trajectory_store, workflows as workflow_store
from mari_server.persistence.postgres import mcp as mcp_repository, knowledge_chats as knowledge_chat_repository
from mari_server.persistence.postgres import sources as source_store, audit as audit_store
from mari_server.persistence.postgres import settings as settings_store, analytics as analytics_store
from mari_server.persistence.postgres import chat as chat_store
from mari_server.persistence.postgres.database import actor_name, jload
from mari_components.knowledge.excerpt import excerpt
from mari_server.product.types import (
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


# Search implementation lives behind the infrastructure boundary.
from mari_server.search.service import MAX_K, hybrid_count, hybrid_search, invalidate_search, like_pattern

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
        document_repository.record_search(detail, window=SEARCH_LOG_WINDOW)
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
        rows, steps = trajectory_store.list_trajectories(cap, start, (category or "").strip() or None)
        if not rows:
            return []
        by_id: dict[int, list[TrajectoryStep]] = {int(row["id"]): [] for row in rows}
        for step in steps:
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
        return trajectory_store.trajectory_count((category or "").strip() or None)

    @strawberry.field
    def trajectory_categories(self) -> list[str]:
        return trajectory_store.trajectory_categories()

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
        return analytics_store.overview(floor)

    @strawberry.field
    def source_pulse(self) -> list[SourcePulse]:
        # bars = real per-source doc-change counts by day (last 12 days, empty
        # days = 0), from documents timestamps; [] when a source has no recent
        # activity — never an invented curve.
        daily, workflow_rows, source_rows = source_store.pulse_inputs()
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
            from mari_server.persistence.postgres import connector_sync
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
        for w in workflow_rows:
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
            for r in source_rows
        ]

    @strawberry.field
    def github_repos(self) -> list[GithubRepo]:
        """Repos visible to the configured token; [] (never errors) without a token."""
        source, connected_rows = source_store.github_configs()
        source_cfg = jload(source["config"]) if source else {}
        available_token = github.configured_token() or str((source_cfg or {}).get("token") or "")
        if not available_token:
            return []
        try:
            repos = github.repositories(available_token)
        except Exception:
            return []
        connected = {jload(r["config"]).get("repo", "") for r in connected_rows}
        return [GithubRepo(
            full_name=r["full_name"], description=r.get("description") or "",
            private=bool(r.get("private")), default_branch=r.get("default_branch") or "main",
            updated_at=r.get("updated_at") or "", connected=r["full_name"] in connected,
        ) for r in repos]

    @strawberry.field
    def sync_status(self, source_id: int) -> SyncStatus:
        project_id = access.require_current_access().project_id
        live = ingest.status(source_id)
        src, counts = source_store.sync_summary(source_id)
        cfg = jload(src["config"]) if src else {}
        # Connector cursors are provider-native and shown as bounded text.
        cursor = cfg.get("cursor") or ""
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
            rows = document_repository.recent(k, offset)
        return [_doc(r) for r in rows]

    @strawberry.field
    def search_total(self, query: str = "") -> int:
        """How many documents `search` would return for this query with no
        limit. The count the results feed puts above the list."""
        if query.strip():
            return hybrid_count(query)
        return document_repository.count()

    @strawberry.field
    def recent_searches(self, limit: int = 6) -> list[str]:
        """The queries this workspace has actually run, newest distinct first.
        Read from usage_log, which the search resolver already writes — nothing
        is recorded here that a person did not type."""
        return document_repository.recent_searches(max(1, limit))

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
        rows = document_repository.related(document_id)
        return [RelatedDoc(id=r["id"], source=r["source"], title=r["title"],
                           rel=r["rel"], direction=r["direction"]) for r in rows]

    @strawberry.field
    def document(self, id: int) -> Document | None:
        row = document_repository.get(id)
        if not row:
            return None
        # Per-user, and it must be the same name toggleWatch writes, or the
        # star never comes back lit for the person who set it.
        return _doc(row, document_repository.is_watched(id, actor_name()))

    @strawberry.field
    def revisions(self, document_id: int) -> list[AuditEvent]:
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in document_repository.revisions(document_id)]

    @strawberry.field
    def findings(self, document_id: int) -> list[Finding]:
        return [Finding(id=r["id"], kind=r["kind"], severity=r["severity"], text=r["text"], note=r["note"])
                for r in document_repository.findings(document_id)]

    @strawberry.field
    def changes(self, document_id: int) -> list[Change]:
        return [Change(id=r["id"], original=r["original"], replacement=r["replacement"],
                       reason=r["reason"], status=r["status"])
                for r in document_repository.changes(document_id)]

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
        rows = lineage_store.graph(project_id)
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
        rows = lineage_store.graph_edges(project_id)
        return [LineageEdge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], kind=r["rel"],
                            date=r["created_at"].isoformat() if r["created_at"] else "",
                            curve=r["curve"], meta=jload(r["meta"])) for r in rows]

    @strawberry.field
    def graph_stats(self) -> GraphStats:
        project_id = access.require_current_access().project_id
        s, contradictions, cited, activity = lineage_store.graph_stats(project_id)
        return GraphStats(
            docs=s["docs"], stale=s["stale"], orphans=s["orphans"],
            untranslated=s["untranslated"], unowned=s["unowned"], contradictions=int(contradictions),
            top_cited=[TopCited(title=r["title"], doc_id=r["id"], inbound=r["inbound"]) for r in cited],
            activity=[ActivityBucket(date=r["day"].isoformat(), count=r["n"]) for r in activity])

    @strawberry.field
    def doc_history(self, document_id: int) -> list[DocHistory]:
        return [DocHistory(at=r["at"], actor=r["actor"], verb=r["verb"], detail=r["target"])
                for r in document_repository.history(document_id)]

    @strawberry.field
    def graph_views(self) -> list[GraphView]:
        project_id = access.require_current_access().project_id
        return [GraphView(id=r["id"], name=r["name"], state=json.dumps(jload(r["state"])))
                for r in knowledge_store.graph_views()]

    @strawberry.field
    def facts(self) -> list[Fact]:
        # verified is facts.verified_at (a DATE) as ISO — not the legacy
        # facts.verified display string, which the console cannot re-format
        # and whose '—' placeholder parses as an invalid date.
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"],
                     verified=r["verified_at"].isoformat() if r["verified_at"] else "")
                for r in knowledge_store.facts()]

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
                for r in knowledge_store.facts(document_id)]

    @strawberry.field
    def fact_contradictions(self) -> list[FactContradiction]:
        """Claims in the ledger that disagree with each other. Deterministic
        (see detect_contradictions) — no model call, and [] on a ledger whose
        claims are consistent, which is the common case."""
        rows = knowledge_store.facts()
        return [FactContradiction(
            fact_id=a["id"], claim=a["claim"], status=a["status"],
            other_fact_id=b["id"], other_claim=b["claim"], other_status=b["status"],
            reason=reason, detail=detail)
            for a, b, reason, detail in detect_contradictions(rows)]

    @strawberry.field
    def tasks(self) -> list[Task]:
        # overdue is derived in SQL against the database's own current_date, so
        # it cannot drift from the due date the same row reports.
        rows = knowledge_store.tasks()
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
        s, tags, people = knowledge_store.task_summary()
        if not s or not s["total"]:
            return None
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
                for r in knowledge_store.digest_topics()]

    @strawberry.field
    def members(self) -> list[Member]:
        return [Member(id=r["id"], name=r["name"], initials=r["initials"], tint=r["tint"],
                       email=r["email"], role=r["role"], provider=r["provider"], status=r["status"],
                       joined=r["joined"].isoformat())
                for r in knowledge_store.members()]

    @strawberry.field
    def glossary(self) -> list[GlossaryTerm]:
        return [GlossaryTerm(id=r["id"], term=r["term"], definition=r["definition"],
                             owner=r["owner_name"], updated=r["updated"].isoformat())
                for r in knowledge_store.glossary_terms()]

    @strawberry.field
    def tag_defs(self) -> list[TagDef]:
        # usage is a real count off the tags table — the Library's tag panel
        # reads "used on N of M documents", so a definition nobody has applied
        # must come back as 0 rather than as a blank the UI can round up.
        return [TagDef(tag=r["tag"], label=r["label"], kind=r["kind"], search_weight=r["search_weight"],
                       is_default=r["is_default"], behaviors=r["behaviors"], usage=int(r["usage"]))
                for r in knowledge_store.tag_definitions()]

    # ——— editorial system: style guides, rule registry, templates, voice ———

    @strawberry.field
    def style_guides(self) -> list[StyleGuide]:
        """The style packs this workspace can adopt. `rules` and `preview` are
        read off `style_rules` in one pass, so a pack's advertised rule count
        is literally the rules it has. A workspace that deleted every shipped
        pack gets [] and the guides tab renders its own empty state."""
        rules: dict[str, list[str]] = {}
        guides, style_rules = knowledge_store.style_guides()
        for r in style_rules:
            rules.setdefault(r["guide_key"], []).append(r["description"])
        return [StyleGuide(key=g["key"], name=g["name"], description=g["description"],
                           tone=g["tone"], builtin=g["builtin"],
                           rules=len(rules.get(g["key"], [])), preview=rules.get(g["key"], []))
                for g in guides]

    @strawberry.field
    def style_rules(self, guide_key: str | None = None) -> list[StyleRule]:
        """The deterministic rule registry, optionally one pack's. The Library's
        rules tab counts this list, so the number on the tab strip is the number
        of rules a document is actually checked against."""
        return [StyleRule(id=r["id"], guide_key=r["guide_key"], family=r["family"],
                          severity=r["severity"], description=r["description"],
                          pack=r["pack"], suggestion=r["suggestion"])
                for r in knowledge_store.style_rules(guide_key)]

    @strawberry.field
    def default_style_pack(self) -> str:
        """The pack this workspace adopted (`style_guide.default_pack`), or ''
        when nobody has chosen one — a real state on a fresh install."""
        v = jload(knowledge_store.setting_value("style_guide")) or {}
        return str(v.get("default_pack") or "") if isinstance(v, dict) else ""

    @strawberry.field
    def voice_layer(self) -> VoiceLayer:
        """The workspace's voice layer (`settings.voice`). Blank and all-off on
        a workspace that has not written one down, which the panel renders as
        empty fields rather than as someone else's voice."""
        v = jload(knowledge_store.setting_value("voice")) or {}
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
                for r in knowledge_store.document_templates()]

    @strawberry.field
    def upload_manifest(self) -> UploadManifest:
        """What the Upload connector ingested, per file: chunks written and how
        many carry a vector. Counted off `chunks` at read time. A workspace that
        has uploaded nothing gets an empty manifest and a '' summary — never a
        sample receipt."""
        rows = knowledge_store.upload_manifest()
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
                for r in knowledge_store.api_keys()]

    @strawberry.field
    def mcp_servers(self) -> list[McpServer]:
        # token is shown once at creation (createMcpServer) — never re-served here.
        return [McpServer(id=r["id"], name=r["name"], url=r["url"], scope=r["scope"],
                          status=r["status"], tools=r["tools"], config=jload(r["config"]),
                          token="")
                for r in mcp_repository.list_servers(access.require_current_access().project_id)]

    @strawberry.field
    def checkpoints(self) -> list[Checkpoint]:
        return [Checkpoint(id=r["id"], provider=r["provider"], item=r["item"], stage=r["stage"],
                           progress=r["progress"], total=r["total"], cursor_id=r["cursor_id"],
                           duration=r["duration"], status=r["status"])
                for r in source_store.checkpoints()]

    @strawberry.field
    def sync_events(self) -> list[SyncEvent]:
        return [SyncEvent(id=r["id"], provider=r["provider"], event=r["event"], detail=r["detail"], at=r["at_label"])
                for r in source_store.sync_events()]

    @strawberry.field
    def audit_log(self, limit: int = 40, query: str = "",
                  date_from: str | None = None, date_to: str | None = None) -> list[AuditEvent]:
        """`query` matches actor, verb or target; `dateFrom`/`dateTo` are ISO
        dates bounding `occurred_at` inclusively. The access log is deeper than
        any window the console can hold, so the filter has to reach the whole
        table rather than narrowing the page already fetched."""
        floor, ceil = _iso_date_arg(date_from), _iso_date_arg(date_to)
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in audit_store.events(query, floor, ceil, limit)]

    @strawberry.field
    def audit_log_total(self, query: str = "",
                        date_from: str | None = None, date_to: str | None = None) -> int:
        """Rows matching the same filter, so the console can say what its window
        is a window onto instead of comparing a filtered view against the
        window's own size. No filter = the whole log."""
        return audit_store.event_count(query, _iso_date_arg(date_from), _iso_date_arg(date_to))

    @strawberry.field
    def activity_feed(self, limit: int = 12) -> list[ActivityItem]:
        """Live feed for the overview: runs, edits, deploys — not chatter."""
        rows = audit_store.activity(limit)
        return [ActivityItem(id=r["id"], kind=r["kind"], actor=r["actor"],
                             text=r["verb"], target=r["target"], at=r["at"],
                             seconds_ago=int(r["seconds_ago"])) for r in rows]

    @strawberry.field
    def workflows(self) -> list[Workflow]:
        return [Workflow(id=r["id"], name=r["name"], description=r["description"], color=r["color"],
                         pinned=r["pinned"], status=r["status"], nodes=jload(r["nodes"]),
                         trigger=jload(r.get("trigger")) or {})
                for r in workflow_store.list_workflows()]

    @strawberry.field
    def workflow_runs(self, workflow_id: int | None = None) -> list[WorkflowRun]:
        rows = workflow_store.list_runs(workflow_id)
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
        r = workflow_store.get_run(id)
        if not r:
            return None
        return WorkflowRun(id=r["id"], workflow_id=r["workflow_id"], workflow_name=r["wf_name"],
                           number=r["number"], status=r["status"],
                           started=r["started_at"].isoformat() if r.get("started_at") else "",
                           duration=r["duration"], progress=r["progress"],
                           stats=jload(r["stats"]), rows=jload(r["rows_data"]),
                           triggered_by=r.get("triggered_by") or "")

    @strawberry.field
    def knowledge_chat_destinations(self) -> list[KnowledgeChatDestination]:
        project_id = access.require_current_access().project_id
        project_slug, rows = knowledge_chat_repository.list_destinations(project_id)
        return [KnowledgeChatDestination(
                    id=r["id"], name=r["name"], slug=r["slug"], title=r["title"],
                    welcome=r["welcome"], status=r["status"],
                    url=f"/knowledge-chat/{project_slug}/{r['slug']}")
                for r in rows]

    @strawberry.field
    def notifications(self) -> list[Notification]:
        """Only the caller's notifications — markNotificationsRead marks rows
        for `actor_name()`, so reading by anything else leaves the badge stuck."""
        return [Notification(id=r["id"], kind=r["kind"], text=r["text"], detail=r["detail"],
                             at=r["at_label"], read=r["read"])
                for r in settings_store.notifications(actor_name())]

    @strawberry.field
    def workspace(self) -> Workspace:
        """The `workspace` settings row, which first-run setup writes and
        Settings → Members edits. Fields nobody has filled in come back "" —
        an unnamed workspace is a real state, not a missing fetch."""
        v = jload(settings_store.value("workspace")) or {}
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
        v = jload(settings_store.value("provisioning")) or {}
        if not isinstance(v, dict):
            v = {}
        team = str(v.get("github_team") or "")
        has_token = bool(github.configured_token())
        synced = settings_store.github_member_count()
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
                for r in settings_store.all_settings()]

    @strawberry.field
    def approved_answers(self) -> list[ApprovedAnswer]:
        return [ApprovedAnswer(id=r["id"], question=r["question"], answer=r["answer"], status=r["status"],
                               owner=r["owner_name"], channels=r["channels"], sources=jload(r["sources"]),
                               served=r["served"], spark=r["spark"],
                               updated=r["updated"].isoformat())
                for r in knowledge_store.approved_answers()]

    @strawberry.field
    def answer_coverage_gaps(self, limit: int = 8) -> list[str]:
        """Questions people actually asked that no approved answer covers.

        Real demand only: search queries logged in usage_log plus questions put
        to the assistant. A question already served by an approved answer is
        not a gap, so anything matching one verbatim is filtered out. Returns
        [] on a workspace nobody has asked anything in — never a sample list."""
        return knowledge_store.answer_coverage_gaps(limit)

    @strawberry.field
    def answer_harvest_sources(self) -> JSON:
        """Real provider-backed inputs available to answer harvesting."""
        from mari_components.connectors import CONNECTOR_CATALOG

        source_counts, chat = knowledge_store.harvest_source_counts()
        counts = {str(row["source"]): int(row["n"]) for row in source_counts if row.get("source")}
        rows = [
            {"key": key, "label": definition.name, "count": counts[key]}
            for key, definition in CONNECTOR_CATALOG.items()
            if counts.get(key, 0) > 0
        ]
        if chat:
            rows.append({"key": "chat", "label": "Chat history", "count": chat})
        return rows

    @strawberry.field
    def index_stats(self) -> JSON:
        """Corpus size as the embedding config page states it: documents, the
        chunks they were split into, and how many of those carry a vector."""
        s = knowledge_store.index_stats() or {"docs": 0, "chunks": 0, "embedded": 0}
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
        from mari_server.destinations import slack as bots
        return bots.bots_status(access.require_current_access())

    @strawberry.field
    def connector_catalog(self) -> JSON:
        """The connector catalog, as GET /connectors/catalog reports it: field
        SPECS only, never stored values (CONNECTORS-CONTRACT.md)."""
        from mari_server.sources import routes as connectors_api
        return connectors_api.catalog()

    @strawberry.field
    def decisions(self) -> list[Decision]:
        rows = knowledge_store.decisions_with_supersession()
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
        counts, drift, fixed = analytics_store.insight_stats(floor, ceil)
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
        rows = source_store.freshness()
        return [FreshnessRow(source=r["source"], provider=r["provider"], fresh=r["fresh"],
                             aging=r["aging"], stale=r["stale"]) for r in rows]

    @strawberry.field
    def readability(self) -> list[ReadabilityRow]:
        rows = knowledge_store.readability()
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
                for r in knowledge_store.glossary_candidates()]

    @strawberry.field
    def audit_runs(self) -> list[AuditRun]:
        return [AuditRun(id=r["id"], provider=r["provider"], repo=r["repo"], findings=r["findings"],
                         fixed=r["fixed"], ran_at=r["ran_at"].isoformat())
                for r in audit_store.repository_runs()]

    @strawberry.field
    def audit_findings(self, run_id: int | None = None) -> list[AuditFinding]:
        return [AuditFinding(id=r["id"], run_id=r["run_id"], kind=r["kind"], title=r["title"],
                             detail=r["detail"], fix_action=r["fix_action"],
                             fix_payload=jload(r["fix_payload"]), status=r["status"])
                for r in audit_store.repository_findings(run_id)]

    @strawberry.field
    def chat_sessions(self, info: strawberry.Info) -> list[ChatSession]:
        access.require_current_access()
        user = info.context.get("user") or {}
        user_id = int(user.get("id") or 0)
        if not user_id:
            return []
        out = []
        for s, msgs in chat_store.sessions_for_owner(user_id):
            out.append(ChatSession(id=s["id"], title=s["title"],
                                   messages=[ChatMessage(id=m["id"], role=m["role"], content=m["content"],
                                                         sources=jload(m["sources"])) for m in msgs]))
        return out

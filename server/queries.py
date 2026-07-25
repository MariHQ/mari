"""Mari Cloud — GraphQL Query root, hybrid search, and lineage layout.

DESIGN.md §4–§5: hybrid search = tsvector rank + pgvector cosine, boosted by
tag weights (tag_definitions.search_weight).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

import strawberry
from strawberry.scalars import JSON

import config
import github
import ingest
import llm
from db import ME, jload, log_usage, q, q1
from gqltypes import (
    ActivityBucket, ActivityItem, ApiKey, ApprovedAnswer, AskMari, AskSource,
    AuditDetail, AuditEvent, AuditFinding, AuditRun, Change, ChatMessage,
    ChatSession, Checkpoint, Decision, DigestImpact, DigestTopic, DigestWhere,
    DocHistory, Document, DocumentTemplate, Fact, FactContradiction, Finding,
    FreshnessRow, GithubRepo, GithubTeamSync, GlossaryCandidate, GlossaryTerm,
    GraphStats, GraphView, InsightStats, LineageEdge, LineageNode, McpServer,
    Member, Notification, Provisioning, ReadabilityRow, Release, Setting, Site,
    SiteFeature, SiteThemePreset, SourcePulse, StyleGuide, StyleRule, SyncEvent,
    SyncStatus, TagDef, Task, TaskSummary, TopCited, UploadFile, UploadManifest,
    VoiceLayer, Workflow, WorkflowRun, Workspace,
)

# ————————————————— lineage layout (LINEAGE-DESIGN.md §3.3) —————————————————

LANES = {"github": (0.14, 0.30), "slack": (0.32, 0.46), "docs": (0.48, 0.66),
         "notion": (0.68, 0.80)}
LANE_OTHER = (0.82, 0.90)
SOURCE_ICONS = {"github": "github", "slack": "slack", "docs": "doc", "notion": "notion"}


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
        id=row["id"], source=row["source"], title=row["title"], snippet=row["snippet"],
        body=row.get("body", ""), kind=row.get("kind", "page"), author=row["author"], author_initials=row["author_initials"],
        # ISO 8601, NOT a display string. The console formats dates itself
        # (components/tokens/format.ts fmtDate) and sorts columns on the raw
        # value, so "Jul 8, 2026" would both double-format and sort
        # alphabetically. The API returns data; the client renders it.
        date=row["updated_src"].isoformat() if row["updated_src"] else "",
        tags=row.get("tags") or [], watched=watched,
    )


DOC_SQL = """
  SELECT d.id, d.source, d.external_id, d.title, d.snippet, d.body, d.author,
         d.author_initials, d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags
  FROM documents d LEFT JOIN tags t ON t.document_id = d.id
  {where}
  GROUP BY d.id ORDER BY d.updated_src DESC NULLS LAST
"""

HYBRID_SQL = """
  WITH scored AS (
    SELECT d.id,
           ts_rank(d.search_vec, plainto_tsquery('english', %(q)s)) AS kw,
           -- semantic score: best chunk match when chunks exist, else doc embedding
           greatest(
             CASE WHEN d.embedding IS NULL OR %(has_vec)s = false THEN 0
                  ELSE 1 - (d.embedding <=> %(vec)s::vector) END,
             CASE WHEN %(has_vec)s = false THEN 0
                  ELSE coalesce((SELECT max(1 - (c.embedding <=> %(vec)s::vector))
                                 FROM chunks c
                                 WHERE c.document_id = d.id AND c.embedding IS NOT NULL), 0) END
           ) AS sim,
           coalesce((SELECT max(td.search_weight) FROM tags tg
                     JOIN tag_definitions td ON td.tag = tg.tag
                     WHERE tg.document_id = d.id), 1.0) AS boost
    FROM documents d
  )
  SELECT d.id, d.source, d.title, d.snippet, d.body, d.author, d.author_initials,
         d.updated_src, d.kind, array_remove(array_agg(t.tag), NULL) AS tags,
         (s.kw * 2.0 + greatest(s.sim - 0.35, 0) * 3.0) * s.boost AS score
  FROM scored s
  JOIN documents d ON d.id = s.id
  LEFT JOIN tags t ON t.document_id = d.id
  WHERE s.kw > 0 OR s.sim > 0.45 OR d.title ILIKE %(like)s
  GROUP BY d.id, s.kw, s.sim, s.boost
  ORDER BY score DESC, d.updated_src DESC NULLS LAST
  LIMIT %(k)s
"""


def hybrid_search(query: str, k: int = 10) -> list[dict]:
    vec = llm.embed(query)
    return q(HYBRID_SQL, {
        "q": query, "vec": str(vec) if vec else "[" + ",".join(["0"] * 768) + "]",
        "has_vec": vec is not None, "like": f"%{query}%", "k": k,
    })


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


# ————————————————— Query —————————————————


@strawberry.type
class Query:
    @strawberry.field
    def overview_stats(self) -> JSON:
        changes = q1("SELECT count(*) AS n FROM events WHERE occurred_at > now() - interval '7 days'")["n"]
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
            import connect_sync
            cfg = jload(r["config"]) or {}
            if r.get("kind") == "connector":
                return connect_sync.masked_config(r["provider"], cfg)
            return {k: v for k, v in cfg.items() if k != "shas"}

        return [
            SourcePulse(id=r["id"], provider=r["provider"], name=r["display_name"], status=r["status"],
                        stat=r["stat_num"], unit=r["stat_unit"], bars=bars(r["id"]),
                        docs_count=r["docs_count"], health=r["health"], config=safe_config(r),
                        kind=r.get("kind") or "",
                        last_sync_at=r["last_sync_at"].isoformat() if r.get("last_sync_at") else "")
            for r in q("SELECT * FROM sources ORDER BY id")
        ]

    @strawberry.field
    def github_repos(self) -> list[GithubRepo]:
        """Repos visible to the configured token; [] (never errors) without a token."""
        if not github.token():
            return []
        try:
            repos = github.list_repos()
        except github.GithubError:
            return []
        connected = {jload(r["config"]).get("repo", "")
                     for r in q("SELECT config FROM sources WHERE kind = 'github'")}
        return [GithubRepo(
            full_name=r["full_name"], description=r.get("description") or "",
            private=bool(r.get("private")), default_branch=r.get("default_branch") or "main",
            updated_at=r.get("updated_at") or "", connected=r["full_name"] in connected,
        ) for r in repos]

    @strawberry.field
    def sync_status(self, source_id: int) -> SyncStatus:
        live = ingest.status(source_id)
        src = q1("SELECT kind, config, last_sync_at FROM sources WHERE id = %s", (source_id,))
        cfg = jload(src["config"]) if src else {}
        # github cursors are commit shas (shown short); connector cursors are
        # provider-native (timestamps, delta tokens) and shown whole.
        cursor = cfg.get("cursor") or ""
        if src and src.get("kind") == "github":
            cursor = cursor[:7]
        counts = q1("""
          SELECT count(DISTINCT d.id) AS docs, count(c.id) AS chunks,
                 count(c.id) FILTER (WHERE c.embedding IS NOT NULL) AS embedded
          FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
          WHERE d.source_id = %s""", (source_id,)) or {"docs": 0, "chunks": 0, "embedded": 0}
        last_sync = src["last_sync_at"].isoformat() if src and src["last_sync_at"] else (cfg.get("last_sync_at") or "")
        return SyncStatus(
            state=live["state"], phase=live["phase"], done=live["done"], total=live["total"],
            last_sync_at=last_sync,
            last_error=live["error"] or cfg.get("last_error", ""),
            cursor=cursor[:40],
            doc_count=int(counts["docs"]), chunk_count=int(counts["chunks"]),
            embedded_count=int(counts["embedded"]))

    @strawberry.field
    def search(self, query: str = "", k: int = 10) -> list[Document]:
        if query.strip():
            log_usage("search", query.strip())
            rows = hybrid_search(query, k)
        else:
            rows = q(DOC_SQL.format(where=""))
        return [_doc(r) for r in rows[:k]]

    @strawberry.field
    def document(self, id: int) -> Document | None:
        rows = q(DOC_SQL.format(where="WHERE d.id = %s"), (id,))
        if not rows:
            return None
        watched = q1("SELECT 1 AS x FROM watches WHERE user_name = %s AND document_id = %s", (ME, id)) is not None
        return _doc(rows[0], watched)

    @strawberry.field
    def revisions(self, document_id: int) -> list[AuditEvent]:
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in q("""SELECT e.* FROM events e JOIN documents d ON e.target = d.title
                              WHERE d.id = %s ORDER BY e.occurred_at DESC LIMIT 20""", (document_id,))]

    @strawberry.field
    def findings(self, document_id: int) -> list[Finding]:
        return [Finding(id=r["id"], kind=r["kind"], severity=r["severity"], text=r["text"], note=r["note"])
                for r in q("SELECT * FROM findings WHERE document_id = %s ORDER BY id", (document_id,))]

    @strawberry.field
    def changes(self, document_id: int) -> list[Change]:
        return [Change(id=r["id"], original=r["original"], replacement=r["replacement"],
                       reason=r["reason"], status=r["status"])
                for r in q("SELECT * FROM changes WHERE document_id = %s ORDER BY id", (document_id,))]

    @strawberry.field
    def lineage(self) -> list[LineageNode]:
        rows = q("""
          SELECT d.id, d.source, d.external_id, d.title, d.author, d.updated_src, d.created_src,
                 d.graph_x, d.graph_y, d.graph_icon, d.graph_meta,
                 d.kind, d.source_path, d.source_id, s.kind AS src_kind,
                 s.config->>'repo' AS repo,
                 array_remove(array_agg(DISTINCT t.tag), NULL) AS tags,
                 (SELECT count(*) FROM edges e WHERE e.to_doc = d.id) AS inbound,
                 (SELECT count(*) FROM edges e WHERE e.from_doc = d.id) AS outbound
          FROM documents d
          LEFT JOIN tags t ON t.document_id = d.id
          LEFT JOIN sources s ON s.id = d.source_id
          GROUP BY d.id, s.kind, s.config ORDER BY d.id""")
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
        rows = q("""
          SELECT e.id, f.external_id AS from_id, t.external_id AS to_id, e.rel, e.created_at,
                 e.curve, e.meta
          FROM edges e JOIN documents f ON f.id = e.from_doc JOIN documents t ON t.id = e.to_doc
          ORDER BY e.id""")
        return [LineageEdge(id=r["id"], from_id=r["from_id"], to_id=r["to_id"], kind=r["rel"],
                            date=r["created_at"].isoformat() if r["created_at"] else "",
                            curve=r["curve"], meta=jload(r["meta"])) for r in rows]

    @strawberry.field
    def graph_stats(self) -> GraphStats:
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
          FROM documents d""")
        contradictions = q1("SELECT count(*) AS n FROM edges WHERE rel = 'contradicts'")["n"]
        cited = q("""SELECT d.title, d.id, count(*) AS inbound FROM edges e
                     JOIN documents d ON d.id = e.to_doc
                     GROUP BY d.id ORDER BY inbound DESC, d.id LIMIT 3""")
        activity = q("""SELECT * FROM (
                          SELECT occurred_at::date AS day, count(*) AS n FROM events
                          GROUP BY 1 ORDER BY 1 DESC LIMIT 60) x ORDER BY day""")
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
        return [GraphView(id=r["id"], name=r["name"], state=json.dumps(jload(r["state"])))
                for r in q("SELECT id, name, state FROM graph_views ORDER BY id")]

    @strawberry.field
    def facts(self) -> list[Fact]:
        # verified is facts.verified_at (a DATE) as ISO — not the legacy
        # facts.verified display string, which the console cannot re-format
        # and whose '—' placeholder parses as an invalid date.
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"],
                     verified=r["verified_at"].isoformat() if r["verified_at"] else "")
                for r in q("SELECT * FROM facts ORDER BY id")]

    @strawberry.field
    def fact_contradictions(self) -> list[FactContradiction]:
        """Claims in the ledger that disagree with each other. Deterministic
        (see detect_contradictions) — no model call, and [] on a ledger whose
        claims are consistent, which is the common case."""
        rows = q("SELECT id, claim, status FROM facts ORDER BY id")
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
                    FROM tasks ORDER BY id""")
        return [Task(id=r["id"], title=r["title"], assignee_initials=r["assignee_initials"],
                     assignee_tint=r["assignee_tint"], kind=r["kind"], kind_label=r["kind_label"],
                     done=r["done"], due=r["due_date"].isoformat() if r["due_date"] else "",
                     overdue=bool(r["overdue"]))
                for r in rows]

    @strawberry.field
    def tasks_summary(self) -> TaskSummary | None:
        """The strip above the board: counts and rosters rolled up off the same
        rows `tasks` returns. None on an empty inbox — there is nothing to
        summarise, and the board renders without a headline."""
        s = q1("""SELECT count(*) AS total,
                         count(*) FILTER (WHERE NOT done) AS open,
                         count(*) FILTER (WHERE done) AS done,
                         count(*) FILTER (WHERE NOT done AND due_date < current_date) AS overdue,
                         count(*) FILTER (WHERE NOT done AND due_date >= current_date
                                          AND due_date <= current_date + 7) AS due_soon
                  FROM tasks""")
        if not s or not s["total"]:
            return None
        tags = [r["kind_label"] for r in q(
            "SELECT DISTINCT kind_label FROM tasks WHERE kind_label <> '' ORDER BY kind_label")]
        people = [r["assignee_initials"] for r in q(
            """SELECT DISTINCT assignee_initials FROM tasks
               WHERE NOT done AND assignee_initials <> '' ORDER BY assignee_initials""")]
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

    @strawberry.field
    def ask_mari(self) -> AskMari:
        r = q("SELECT * FROM ask_answers ORDER BY id DESC LIMIT 1")[0]
        return AskMari(question=r["question"], answer=r["answer"],
                       sources=[AskSource(**s) for s in jload(r["sources"])])

    @strawberry.field
    def digest(self) -> list[DigestTopic]:
        return [DigestTopic(title=r["title"], summary=r["summary"],
                            where=[DigestWhere(**w) for w in jload(r["wheres"])],
                            impact=[DigestImpact(**i) for i in jload(r["impact"])])
                for r in q("SELECT * FROM digest_topics ORDER BY id")]

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
                for r in q("SELECT * FROM glossary ORDER BY term")]

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
        rows = q("""SELECT d.id, d.source_path, d.external_id, d.updated_src,
                           count(c.id) AS chunks,
                           count(c.embedding) AS embedded
                    FROM documents d
                    JOIN sources s ON s.id = d.source_id AND s.provider = 'upload'
                    LEFT JOIN chunks c ON c.document_id = d.id
                    GROUP BY d.id ORDER BY d.id""")
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

    # ——— doc-site presentation: theme presets, generator switches ———

    @strawberry.field
    def site_theme_presets(self) -> list[SiteThemePreset]:
        """Themes the generator can render, with the accent each one ships when
        a site has not overridden it. sitebuilder reads the same rows."""
        return [SiteThemePreset(key=r["key"], name=r["name"], accent=r["accent"], bg=r["bg"])
                for r in q("SELECT * FROM site_theme_presets ORDER BY sort, key")]

    @strawberry.field
    def site_features(self, site_id: int | None = None) -> list[SiteFeature]:
        """The generator's switches. Without a site id, `on` is the shipped
        default; with one, the site's stored override wins. Every key here is
        read by sitebuilder — the page never offers a toggle that changes
        nothing about the built site."""
        overrides: dict = {}
        if site_id:
            row = q1("SELECT features FROM sites WHERE id = %s", (site_id,))
            if row:
                overrides = jload(row["features"]) or {}
        return [SiteFeature(key=r["key"], label=r["label"], hint=r["hint"],
                            on=bool(overrides.get(r["key"], r["default_on"])))
                for r in q("SELECT * FROM site_feature_defs ORDER BY sort, key")]

    @strawberry.field
    def api_keys(self) -> list[ApiKey]:
        return [ApiKey(id=r["id"], name=r["name"], prefix=r["prefix"], scopes=r["scopes"],
                       created=r["created_at"].isoformat(), last_used=r["last_used"], revoked=r["revoked"])
                for r in q("SELECT * FROM api_keys ORDER BY id")]

    @strawberry.field
    def mcp_servers(self) -> list[McpServer]:
        # token is shown once at creation (createMcpServer) — never re-served here.
        return [McpServer(id=r["id"], name=r["name"], url=r["url"], scope=r["scope"],
                          status=r["status"], tools=r["tools"], config=jload(r["config"]),
                          token=_mask_secret(r["token"]))
                for r in q("SELECT * FROM mcp_servers ORDER BY id")]

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
    def audit_log(self, limit: int = 40) -> list[AuditEvent]:
        return [AuditEvent(id=r["id"], actor=r["actor"], verb=r["verb"], target=r["target"],
                           at=r["occurred_at"].isoformat(), detail=_details(r))
                for r in q("SELECT * FROM events ORDER BY occurred_at DESC, id DESC LIMIT %s", (limit,))]

    @strawberry.field
    def audit_log_total(self) -> int:
        """Rows in the whole log, so the console can say what its window is a
        window onto instead of comparing a filtered view against the window."""
        return int(q1("SELECT count(*) AS n FROM events")["n"])

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
          FROM events ORDER BY occurred_at DESC, id DESC LIMIT %s
        """, (limit,))
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
    def sites(self) -> list[Site]:
        return [Site(id=r["id"], name=r["name"], domain=r["domain"], status=r["status"],
                     theme=jload(r["theme"]), sources=jload(r["sources"]), nav=jload(r["nav"]),
                     gates=jload(r["gates"]), docs=r["docs"], warnings=r["warnings"])
                for r in q("SELECT * FROM sites ORDER BY id")]

    @strawberry.field
    def releases(self, site_id: int | None = None) -> list[Release]:
        """Release history. Omit site_id for every site's, so the Publish page
        can fetch sites and their releases in one document instead of chaining
        a second round trip on the first site's id."""
        where = "WHERE site_id = %s" if site_id else ""
        return [Release(id=r["id"], site_id=r["site_id"], version=r["version"], status=r["status"],
                        deployed=r["deployed"], docs=r["docs"], notes=r["notes"])
                for r in q(f"SELECT * FROM releases {where} ORDER BY site_id, id DESC",
                           (site_id,) if site_id else ())]

    @strawberry.field
    def notifications(self) -> list[Notification]:
        return [Notification(id=r["id"], kind=r["kind"], text=r["text"], detail=r["detail"],
                             at=r["at_label"], read=r["read"])
                for r in q("SELECT * FROM notifications WHERE user_name = %s ORDER BY id", (ME,))]

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
        has_token = bool(github.token())
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
        return [Setting(key=r["key"], value=_mask_setting(r["key"], jload(r["value"])))
                for r in q("SELECT * FROM settings ORDER BY key")]

    @strawberry.field
    def approved_answers(self) -> list[ApprovedAnswer]:
        return [ApprovedAnswer(id=r["id"], question=r["question"], answer=r["answer"], status=r["status"],
                               owner=r["owner_name"], channels=r["channels"], sources=jload(r["sources"]),
                               served=r["served"], spark=r["spark"],
                               updated=r["updated"].isoformat())
                for r in q("SELECT * FROM approved_answers ORDER BY (status = 'approved') DESC, served DESC")]

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
    def index_stats(self) -> JSON:
        """Corpus size as the embedding config page states it: documents, the
        chunks they were split into, and how many of those carry a vector."""
        s = q1("""SELECT (SELECT count(*) FROM documents) AS docs,
                         count(*) AS chunks,
                         count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded
                  FROM chunks""") or {"docs": 0, "chunks": 0, "embedded": 0}
        return {"docs": int(s["docs"]), "chunks": int(s["chunks"]), "embedded": int(s["embedded"])}

    @strawberry.field
    def bots_status(self) -> JSON:
        """Slack + GitHub bot wiring, as GET /bots/status reports it. Same
        function, so the console's Sources page and the REST surface can never
        disagree about whether a bot is configured."""
        import bots
        return bots.bots_status()

    @strawberry.field
    def connector_catalog(self) -> JSON:
        """The connector catalog, as GET /connectors/catalog reports it: field
        SPECS only, never stored values (CONNECTORS-CONTRACT.md)."""
        import connectors_api
        return connectors_api.catalog()

    @strawberry.field
    def decisions(self) -> list[Decision]:
        rows = q("""SELECT d.*, s.statement AS sup_stmt FROM decisions d
                    LEFT JOIN decisions s ON s.id = d.superseded_by ORDER BY d.id DESC""")
        return [Decision(id=r["id"], statement=r["statement"], context=r["context"], status=r["status"],
                         source_label=r["source_label"], owners=r["owners"],
                         decided_on=r["decided_on"].isoformat() if r["decided_on"] else "",
                         superseded_by=r["superseded_by"], superseded_by_statement=r["sup_stmt"] or "",
                         impact_summary=r["impact_summary"], impact_count=r["impact_count"]) for r in rows]

    @strawberry.field
    def insight_stats(self) -> InsightStats:
        """Every number is a real count (BOTS-CONTRACT.md §A) — never a constant."""
        counts = q1("""
          SELECT count(*) FILTER (WHERE kind = 'search') AS searches,
                 count(*) FILTER (WHERE kind = 'chat_answer') AS served,
                 min(at) AS since
          FROM usage_log""") or {"searches": 0, "served": 0, "since": None}
        drift = q1("SELECT count(*) AS n FROM findings WHERE kind IN ('fact','freshness')")["n"]
        fixed = q1("SELECT count(*) AS n FROM changes WHERE status = 'accepted'")["n"]
        return InsightStats(searches=int(counts["searches"]), answers_served=int(counts["served"]),
                            drift_caught=int(drift), docs_fixed=int(fixed),
                            since=counts["since"].isoformat() if counts["since"] else "")

    @strawberry.field
    def freshness(self) -> list[FreshnessRow]:
        """Rollup over connected sources only (docs with a source_id) — one row
        per live sources row; seed/sample docs never masquerade as a provider."""
        rows = q("""
          WITH bucketed AS (
            SELECT d.source_id,
                   CASE WHEN d.updated_src IS NULL OR d.updated_src < current_date - 30
                          OR EXISTS (SELECT 1 FROM tags t WHERE t.document_id = d.id AND t.tag = 'stale')
                        THEN 'stale'
                        WHEN d.updated_src < current_date - 7 THEN 'aging'
                        ELSE 'fresh' END AS bucket
            FROM documents d WHERE d.source_id IS NOT NULL)
          SELECT s.display_name AS source,
                 count(*) FILTER (WHERE b.bucket = 'fresh') AS fresh,
                 count(*) FILTER (WHERE b.bucket = 'aging') AS aging,
                 count(*) FILTER (WHERE b.bucket = 'stale') AS stale
          FROM sources s LEFT JOIN bucketed b ON b.source_id = s.id
          GROUP BY s.id, s.display_name ORDER BY count(b.source_id) DESC, s.id""")
        return [FreshnessRow(source=r["source"], fresh=r["fresh"], aging=r["aging"], stale=r["stale"]) for r in rows]

    @strawberry.field
    def readability(self) -> list[ReadabilityRow]:
        rows = q("SELECT id, title, source, readability FROM documents ORDER BY id")
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
                for r in q("SELECT * FROM glossary WHERE candidate ORDER BY id")]

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
    def chat_sessions(self) -> list[ChatSession]:
        out = []
        for s in q("SELECT * FROM chat_sessions ORDER BY id DESC LIMIT 20"):
            msgs = q("SELECT * FROM chat_messages WHERE session_id = %s ORDER BY id", (s["id"],))
            out.append(ChatSession(id=s["id"], title=s["title"],
                                   messages=[ChatMessage(id=m["id"], role=m["role"], content=m["content"],
                                                         sources=jload(m["sources"])) for m in msgs]))
        return out

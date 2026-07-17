"""Mari Cloud — GraphQL Query root, hybrid search, and lineage layout.

DESIGN.md §4–§5: hybrid search = tsvector rank + pgvector cosine, boosted by
tag weights (tag_definitions.search_weight).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import strawberry
from strawberry.scalars import JSON

import github
import ingest
import llm
from db import ME, jload, log_usage, q, q1
from gqltypes import (
    ActivityBucket, ActivityItem, ApiKey, ApprovedAnswer, AskMari, AskSource,
    AuditEvent, AuditFinding, AuditRun, Change, ChatMessage, ChatSession,
    Checkpoint, Decision, DigestImpact, DigestTopic, DigestWhere, DocHistory,
    Document, Fact, Finding, FreshnessRow, GithubRepo, GlossaryCandidate,
    GlossaryTerm, GraphStats, GraphView, InsightStats, LineageEdge, LineageNode,
    McpServer, Member, Notification, ReadabilityRow, Release, Setting, Site,
    SourcePulse, SyncEvent, SyncStatus, TagDef, Task, TopCited, Workflow,
    WorkflowRun,
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
        date=row["updated_src"].strftime("%b %-d, %Y") if row["updated_src"] else "",
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


def _mask_setting(key: str, value):
    if key == "setup_token":
        return _mask_secret(value if isinstance(value, str) else json.dumps(value))
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
                        docs_count=r["docs_count"], health=r["health"], config=safe_config(r))
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
                           at=r["occurred_at"].strftime("%b %-d, %-I:%M %p"))
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
                                     to_char(e.occurred_at, 'YYYY-MM-DD HH24:MI') AS at
                              FROM events e JOIN documents d ON e.target = d.title
                              WHERE d.id = %s ORDER BY e.occurred_at DESC LIMIT 30""", (document_id,))]

    @strawberry.field
    def graph_views(self) -> list[GraphView]:
        return [GraphView(id=r["id"], name=r["name"], state=json.dumps(jload(r["state"])))
                for r in q("SELECT id, name, state FROM graph_views ORDER BY id")]

    @strawberry.field
    def facts(self) -> list[Fact]:
        return [Fact(id=r["id"], claim=r["claim"], source=r["source"], owner=r["owner_name"],
                     owner_tint=r["owner_tint"], status=r["status"], verified=r["verified"])
                for r in q("SELECT * FROM facts ORDER BY id")]

    @strawberry.field
    def tasks(self) -> list[Task]:
        return [Task(id=r["id"], title=r["title"], assignee_initials=r["assignee_initials"],
                     assignee_tint=r["assignee_tint"], kind=r["kind"], kind_label=r["kind_label"], done=r["done"])
                for r in q("SELECT * FROM tasks ORDER BY id")]

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
                       joined=r["joined"].strftime("%b %-d, %Y"))
                for r in q("SELECT * FROM users ORDER BY id")]

    @strawberry.field
    def glossary(self) -> list[GlossaryTerm]:
        return [GlossaryTerm(id=r["id"], term=r["term"], definition=r["definition"],
                             owner=r["owner_name"], updated=r["updated"].strftime("%b %-d, %Y"))
                for r in q("SELECT * FROM glossary ORDER BY term")]

    @strawberry.field
    def tag_defs(self) -> list[TagDef]:
        return [TagDef(tag=r["tag"], label=r["label"], kind=r["kind"], search_weight=r["search_weight"],
                       is_default=r["is_default"], behaviors=r["behaviors"])
                for r in q("SELECT * FROM tag_definitions ORDER BY search_weight DESC")]

    @strawberry.field
    def api_keys(self) -> list[ApiKey]:
        return [ApiKey(id=r["id"], name=r["name"], prefix=r["prefix"], scopes=r["scopes"],
                       created=r["created_at"].strftime("%b %-d, %Y"), last_used=r["last_used"], revoked=r["revoked"])
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
                           at=r["occurred_at"].strftime("%b %-d, %-I:%M %p"))
                for r in q("SELECT * FROM events ORDER BY occurred_at DESC, id DESC LIMIT %s", (limit,))]

    @strawberry.field
    def activity_feed(self, limit: int = 12) -> list[ActivityItem]:
        """Live feed for the overview: runs, edits, deploys — not chatter."""
        rows = q("""
          SELECT id, actor, verb, target,
                 to_char(occurred_at, 'HH24:MI') AS at,
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
                             text=r["verb"], target=r["target"], at=r["at"]) for r in rows]

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
                            number=r["number"], status=r["status"], started=r["started_label"],
                            duration=r["duration"], progress=r["progress"],
                            stats=jload(r["stats"]), rows=jload(r["rows_data"]),
                            triggered_by=r.get("triggered_by") or "") for r in rows]

    @strawberry.field
    def sites(self) -> list[Site]:
        return [Site(id=r["id"], name=r["name"], domain=r["domain"], status=r["status"],
                     theme=jload(r["theme"]), sources=jload(r["sources"]), nav=jload(r["nav"]),
                     gates=jload(r["gates"]), docs=r["docs"], warnings=r["warnings"])
                for r in q("SELECT * FROM sites ORDER BY id")]

    @strawberry.field
    def releases(self, site_id: int) -> list[Release]:
        return [Release(id=r["id"], site_id=r["site_id"], version=r["version"], status=r["status"],
                        deployed=r["deployed"], docs=r["docs"], notes=r["notes"])
                for r in q("SELECT * FROM releases WHERE site_id = %s ORDER BY id DESC", (site_id,))]

    @strawberry.field
    def notifications(self) -> list[Notification]:
        return [Notification(id=r["id"], kind=r["kind"], text=r["text"], detail=r["detail"],
                             at=r["at_label"], read=r["read"])
                for r in q("SELECT * FROM notifications WHERE user_name = %s ORDER BY id", (ME,))]

    @strawberry.field
    def settings(self) -> list[Setting]:
        return [Setting(key=r["key"], value=_mask_setting(r["key"], jload(r["value"])))
                for r in q("SELECT * FROM settings ORDER BY key")]

    @strawberry.field
    def approved_answers(self) -> list[ApprovedAnswer]:
        return [ApprovedAnswer(id=r["id"], question=r["question"], answer=r["answer"], status=r["status"],
                               owner=r["owner_name"], channels=r["channels"], sources=jload(r["sources"]),
                               served=r["served"], spark=r["spark"],
                               updated=r["updated"].strftime("%b %-d, %Y"))
                for r in q("SELECT * FROM approved_answers ORDER BY (status = 'approved') DESC, served DESC")]

    @strawberry.field
    def decisions(self) -> list[Decision]:
        rows = q("""SELECT d.*, s.statement AS sup_stmt FROM decisions d
                    LEFT JOIN decisions s ON s.id = d.superseded_by ORDER BY d.id DESC""")
        return [Decision(id=r["id"], statement=r["statement"], context=r["context"], status=r["status"],
                         source_label=r["source_label"], owners=r["owners"],
                         decided_on=r["decided_on"].strftime("%b %-d, %Y") if r["decided_on"] else "",
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
        return [GlossaryCandidate(id=r["id"], term=r["term"], variants=r["variants"], definition=r["definition"])
                for r in q("SELECT * FROM glossary WHERE candidate ORDER BY id")]

    @strawberry.field
    def audit_runs(self) -> list[AuditRun]:
        return [AuditRun(id=r["id"], provider=r["provider"], repo=r["repo"], findings=r["findings"],
                         fixed=r["fixed"], ran_at=r["ran_at"].strftime("%b %-d, %-I:%M %p"))
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

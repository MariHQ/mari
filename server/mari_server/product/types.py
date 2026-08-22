"""Mari — GraphQL type definitions.

Dates and timestamps are **ISO 8601 strings**, never pre-formatted for display.
The console renders every date through the component library's own `fmtDate` /
`fmtDateTime` / `fmtAgo`, and its sortable tables sort on the raw field value —
so a server-formatted "Jul 8, 2026" both double-formats and sorts
alphabetically (Apr before Jan). The API returns data; the client renders it.

Empty string means "no date", which is distinct from a date the client failed
to fetch.
"""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class SourcePulse:
    id: int
    provider: str
    name: str
    status: str
    stat: str
    unit: str
    bars: list[int]
    docs_count: int
    health: str
    config: JSON
    # '' for rows the onboarding seeded but no connector owns; 'github',
    # 'upload' or 'connector' once one does. The console reads it to tell a
    # source it can report live sync state for from one it can only show the
    # seeded document count for.
    kind: str
    last_sync_at: str  # ISO 8601, '' when the source has never synced
    # How often this source re-syncs itself, in minutes, and the flow that says
    # so. Every connected source gets a schedule-triggered "Sync <name>" flow
    # (flowengine.ensure_sync_flow), so the cadence is that flow's trigger —
    # NULL when the flow is paused, manual-only, or does not exist, which is
    # the honest reading of "this source has no automatic schedule".
    sync_interval_minutes: int | None
    sync_flow_id: int | None


@strawberry.type
class Document:
    id: int
    source: str
    title: str
    snippet: str
    body: str
    kind: str
    author: str
    author_initials: str
    date: str
    tags: list[str]
    watched: bool


@strawberry.type
class RelatedDoc:
    """A document one lineage edge away from another document.

    The Knowledge inspector's "Related docs" section used to have no source at
    all: edges were only exposed as the whole graph, so nothing could answer
    "what links to THIS document". This is that answer — real `edges` rows,
    both directions, with the relation the edge records."""
    id: int
    source: str
    title: str
    rel: str          # the raw edges.rel word; the client maps it to its own vocabulary
    direction: str    # "out" (this doc → other) | "in" (other → this doc)


@strawberry.type
class LineageNode:
    id: str
    doc_id: int
    source: str
    title: str
    meta: str
    icon: str
    x: float
    y: float
    pinned: bool
    date: str
    created_date: str
    warn: bool
    owner: str
    tags: list[str]
    stale_days: int
    orphan: bool
    inbound: int
    outbound: int
    # LINEAGE-ROLLUP-CONTRACT.md §1: node classification + roll-up bucket
    doc_kind: str  # page|commit|pr|issue|answer|decision|seed
    group: str     # "" for ungrouped, else e.g. "gh:MariHQ/mari-cli:commits"


@strawberry.type
class LineageEdge:
    id: int
    from_id: str
    to_id: str
    kind: str
    date: str
    curve: float | None
    meta: JSON


@strawberry.type
class TopCited:
    title: str
    doc_id: int
    inbound: int


@strawberry.type
class ActivityBucket:
    date: str
    count: int


@strawberry.type
class GraphStats:
    docs: int
    stale: int
    orphans: int
    untranslated: int
    unowned: int
    contradictions: int
    top_cited: list[TopCited]
    activity: list[ActivityBucket]


@strawberry.type
class DocHistory:
    at: str
    actor: str
    verb: str
    detail: str


@strawberry.type
class GraphView:
    id: int
    name: str
    state: str


@strawberry.type
class Fact:
    id: int
    claim: str
    source: str
    owner: str
    owner_tint: int
    status: str
    verified: str  # ISO date of the last verification, "" when never verified


@strawberry.type
class FactContradiction:
    """Two stored claims that disagree. Both sides are real `facts` rows —
    the detector never writes a claim, it only pairs the ones already there."""
    fact_id: int
    claim: str
    status: str
    other_fact_id: int
    other_claim: str
    other_status: str
    reason: str   # numeric|polarity
    detail: str   # the values that disagree, e.g. "100 vs 250"


@strawberry.type
class Task:
    id: int
    title: str
    assignee_initials: str
    assignee_tint: int
    kind: str
    kind_label: str
    done: bool
    due: str       # ISO date, "" when the task has no deadline
    overdue: bool  # due date is in the past and the task is still open
    # A denormalized, typed pointer to the item under review. Empty strings are
    # the backwards-compatible no-subject value for tasks created before this
    # metadata existed.
    subject_type: str
    subject_id: str
    subject_title: str
    subject_href: str


@strawberry.type
class TaskSummary:
    """The rollup strip above the board. Every field is counted off the same
    `tasks` rows the board renders, so the strip can never disagree with it."""
    title: str
    tags: list[str]     # the kind labels present on the board
    people: list[str]   # initials of everyone holding an open task
    stat_value: str
    stat_label: str
    open_count: int
    done_count: int
    overdue_count: int
    due_soon_count: int  # open, due within 7 days, not yet overdue


@strawberry.type
class ReviewItem:
    id: str
    kind: str
    title: str
    status: str
    source: str
    assignee: str
    due: str
    subject_type: str
    subject_id: str
    subject_title: str
    subject_href: str
    confidence: float
    evidence_count: int
    trusted_source: bool


@strawberry.type
class ReviewPageInfo:
    end_cursor: str
    has_next_page: bool


@strawberry.type
class ReviewConnection:
    items: list[ReviewItem]
    total_count: int
    page_info: ReviewPageInfo


@strawberry.type
class ReviewPolicyDecision:
    review_id: str
    outcome: str
    explanation: str
    policy_version: str
    replayed: bool
    dry_run: bool


@strawberry.type
class DigestWhere:
    source: str
    label: str


@strawberry.type
class DigestImpact:
    name: str
    tone: str


@strawberry.type
class DigestTopic:
    title: str
    summary: str
    where: list[DigestWhere]
    impact: list[DigestImpact]


@strawberry.type
class Member:
    id: int
    name: str
    initials: str
    tint: int
    email: str
    role: str
    provider: str
    status: str
    joined: str


@strawberry.type
class GlossaryTerm:
    id: int
    term: str
    definition: str
    owner: str
    updated: str


@strawberry.type
class StyleRule:
    """One rule in the deterministic prose registry (`style_rules`). `pack` is
    the code a finding cites; the pattern that detects the rule lives with the
    checker, not here, so there is only ever one implementation of it."""
    id: str
    guide_key: str
    family: str
    severity: str  # error|warn|advisory
    description: str
    pack: str
    suggestion: str


@strawberry.type
class StyleGuide:
    """A pack of style rules a workspace can adopt as its default. `rules` and
    `preview` are counted and read off `style_rules`, never stored beside the
    guide, so a pack can never advertise a rule it does not contain."""
    key: str
    name: str
    description: str
    tone: str      # ink|ok|attention|blocked|info — a colour, not a claim
    builtin: bool  # shipped with the product vs. written by this workspace
    rules: int
    preview: list[str]  # every rule's description, in registry order


@strawberry.type
class VoiceLayer:
    """The workspace's own voice, stacked on whichever pack it adopted. Blank
    and all-off until someone writes one down (settings.voice)."""
    voice: str
    terms: str
    banned: str
    inclusive: bool
    jargon: bool
    sentence_case: bool


@strawberry.type
class DocumentTemplate:
    key: str
    name: str
    category: str
    description: str
    sections: list[str]  # the headings a draft opens with, in order
    icon: str            # clipboard|git-fork|shield-check|file-text|sprout|book-open|megaphone
    standard: bool       # shipped with the product vs. created by this workspace


@strawberry.type
class UploadFile:
    """One file the Upload connector ingested, with what the pipeline did to
    it. Counted off `chunks` at read time, so the manifest cannot drift from
    the index — a re-upload of unchanged content re-embeds nothing and the
    embedded count stays where it was."""
    name: str
    doc_id: int
    chunks: int
    embedded: int
    detail: str        # "12 chunks · 12 embedded", the line the file row shows
    ingested_at: str   # ISO 8601 date the document was last ingested


@strawberry.type
class UploadManifest:
    files: list[UploadFile]
    file_count: int
    chunk_count: int
    embedded_count: int
    summary: str  # "3 files · 41 chunks · 41 embedded"; '' when nothing is uploaded


@strawberry.type
class TagDef:
    tag: str
    label: str
    kind: str
    search_weight: float
    is_default: bool
    behaviors: str
    usage: int  # documents currently carrying the tag


@strawberry.type
class ApiKey:
    id: int
    name: str
    prefix: str
    scopes: str
    created: str
    last_used: str
    revoked: bool


@strawberry.type
class McpServer:
    id: int
    name: str
    url: str
    scope: str
    status: str
    tools: int
    config: JSON
    token: str


@strawberry.type
class Checkpoint:
    id: int
    provider: str
    item: str
    stage: str
    progress: int
    total: int
    cursor_id: str
    duration: str
    status: str


@strawberry.type
class SyncEvent:
    id: int
    provider: str
    event: str
    detail: str
    at: str


@strawberry.type
class GithubRepo:
    full_name: str
    description: str
    private: bool
    default_branch: str
    updated_at: str
    connected: bool


@strawberry.type
class SyncStatus:
    state: str
    phase: str
    done: int
    total: int
    last_sync_at: str
    last_error: str
    cursor: str
    doc_count: int
    chunk_count: int
    embedded_count: int


@strawberry.type
class AuditDetail:
    label: str
    value: str


@strawberry.type
class AuditEvent:
    id: int
    actor: str
    verb: str
    target: str
    at: str
    # What the expanded row shows beyond actor/verb/target/at. Recorded at
    # write time by whoever logged the event (db.audit), so it is [] for
    # events logged before the writer had anything more to say.
    detail: list[AuditDetail]


@strawberry.type
class ActivityItem:
    id: int
    kind: str          # run|edit|deploy|fact|task|sync|link
    actor: str
    text: str
    target: str
    at: str            # wall-clock "HH:MM", for dense timestamped lists
    seconds_ago: int   # age at query time, for relative "5m ago" rendering


@strawberry.type
class Finding:
    id: int
    kind: str
    severity: str
    text: str
    note: str


@strawberry.type
class Change:
    id: int
    original: str
    replacement: str
    reason: str
    status: str


@strawberry.type
class Workflow:
    id: int
    name: str
    description: str
    color: str
    pinned: bool
    status: str
    nodes: JSON
    # {"on": "document_changed"|"document_added"|"", "source_id", "tag", "path_glob"};
    # empty "on" = manual-only.
    trigger: JSON


@strawberry.type
class WorkflowRun:
    id: int
    workflow_id: int
    workflow_name: str
    number: int
    status: str
    started: str
    duration: str
    progress: int
    stats: JSON
    rows: JSON
    triggered_by: str  # provenance for auto-started runs, '' for manual


@strawberry.type
class KnowledgeChatDestination:
    id: int
    name: str
    slug: str
    title: str
    welcome: str
    status: str
    url: str
    tools: JSON


@strawberry.type
class Notification:
    id: int
    kind: str
    text: str
    detail: str
    at: str
    read: bool


@strawberry.type
class Setting:
    key: str
    value: JSON


@strawberry.type
class Workspace:
    """Workspace identity, from the `workspace` settings row. A fresh install
    has no name until first-run setup or an admin supplies one — "" then, and
    the console renders its own unnamed-workspace treatment."""
    name: str
    slug: str
    plan: str
    timezone: str
    language: str


@strawberry.type
class GithubTeamSync:
    team: str          # "org/team", "" until an admin configures one
    connected: bool    # a team is configured AND a GitHub credential exists
    credential: bool   # the server has a GitHub token at all
    synced_members: int  # users whose account came from GitHub


@strawberry.type
class Provisioning:
    """How members get into this workspace. Every flag reflects a mechanism
    the server actually implements — nothing here is aspirational."""
    manual_invites: bool
    github_team: GithubTeamSync
    sso_providers: list[str]  # OAuth providers with credentials configured
    sso_enabled: bool
    scim_enabled: bool
    scim_status: str  # 'unavailable' — no SCIM endpoint exists to enable


@strawberry.type
class ChatMessage:
    id: int
    role: str
    content: str
    sources: JSON


@strawberry.type
class ChatSession:
    id: int
    title: str
    messages: list[ChatMessage]


@strawberry.type
class TrajectoryStep:
    ordinal: int
    tool: str
    action_family: str
    args: JSON
    summary: str
    ok: bool
    disposition: str
    edited_args: JSON | None


@strawberry.type
class TrajectoryEvidence:
    document_id: int
    title: str
    reason: str
    rank: int
    relevance: str
    note: str


@strawberry.type
class Trajectory:
    id: int
    session_id: int | None
    prompt: str
    status: str
    model: str
    layer1: str
    layer2: str
    category: str
    macro_intent: str
    phases: JSON
    step_count: int
    failure_count: int
    rework_count: int
    started_at: str
    completed_at: str
    selected_workflow_id: int | None
    selected_workflow_score: float | None
    selected_workflow_exact: bool
    execution_mode: str
    observed_cluster_id: int | None
    steps: list[TrajectoryStep]
    evidence: list[TrajectoryEvidence]
    promoted_workflow_id: int | None
    promoted_workflow_name: str
    workflow_root_trajectory_id: int | None
    workflow_observation_count: int
    promoted_workflow_status: str
    promoted_workflow_cache_policy: str
    promoted_workflow_cache_state: str
    promoted_workflow_cache_refreshed_at: str
    promoted_workflow_dependency_count: int
    promoted_workflow_embedding_map: JSON


@strawberry.type
class ApprovedAnswer:
    id: int
    question: str
    answer: str
    status: str
    owner: str
    channels: list[str]
    sources: JSON
    served: int
    spark: list[int]
    updated: str


@strawberry.type
class Decision:
    id: int
    statement: str
    context: str
    status: str
    source_label: str
    owners: list[str]
    decided_on: str
    superseded_by: int | None
    superseded_by_statement: str
    impact_summary: str
    impact_count: int


@strawberry.type
class InsightStats:
    searches: int
    answers_served: int
    drift_caught: int
    docs_fixed: int
    since: str  # ISO timestamp of the earliest usage_log row; "" when no usage yet


@strawberry.type
class FreshnessRow:
    source: str        # the source's display name
    provider: str      # provider key ('github', 'slack'), for the brand mark
    fresh: int
    aging: int
    stale: int


@strawberry.type
class ReadabilityRow:
    id: int
    title: str
    source: str
    grade: str
    note: str


@strawberry.type
class GlossaryCandidate:
    id: int
    term: str
    variants: str
    definition: str
    # Where the term was actually found. The harvester only keeps a candidate
    # whose text appears in a real document, and records that document's title
    # here; '' (with evidence_doc_id 0) for a term someone typed in by hand,
    # which has no source document to cite.
    evidence: str
    evidence_doc_id: int


@strawberry.type
class AuditRun:
    id: int
    provider: str
    repo: str
    findings: int
    fixed: int
    ran_at: str


@strawberry.type
class AuditFinding:
    id: int
    run_id: int
    kind: str
    title: str
    detail: str
    fix_action: str
    fix_payload: JSON
    status: str


@strawberry.type
class ImpactDoc:
    title: str
    source: str
    severity: str
    reason: str


@strawberry.type
class ImpactResult:
    claim: str
    summary: str
    docs: list[ImpactDoc]


@strawberry.type
class AnswerCandidate:
    question: str
    draft_answer: str
    source_label: str
    confidence: str

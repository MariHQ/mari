"""Mari Cloud — GraphQL type definitions (existing shapes kept stable for the frontend)."""

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
    verified: str


@strawberry.type
class Task:
    id: int
    title: str
    assignee_initials: str
    assignee_tint: int
    kind: str
    kind_label: str
    done: bool


@strawberry.type
class AskSource:
    n: int
    source: str
    name: str
    meta: str
    date: str
    text: str
    by: str
    tags: list[str]


@strawberry.type
class AskMari:
    question: str
    answer: str
    sources: list[AskSource]


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
class TagDef:
    tag: str
    label: str
    kind: str
    search_weight: float
    is_default: bool
    behaviors: str


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
class AuditEvent:
    id: int
    actor: str
    verb: str
    target: str
    at: str


@strawberry.type
class ActivityItem:
    id: int
    kind: str          # run|edit|deploy|fact|task|sync|link
    actor: str
    text: str
    target: str
    at: str


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
class Site:
    id: int
    name: str
    domain: str
    status: str
    theme: JSON
    sources: JSON
    nav: JSON
    gates: JSON
    docs: int
    warnings: int


@strawberry.type
class Release:
    id: int
    site_id: int
    version: str
    status: str
    deployed: str
    docs: int
    notes: str


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
    source: str
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

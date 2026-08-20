"""Unified Review projection and deterministic automated-approval policy.

Review rows remain owned by their existing tables.  This module projects them
into one stable shape and records policy decisions separately; no source row is
moved or rewritten merely because it appears in Review.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import datetime as dt
import hashlib
import json
from typing import Callable, Iterable

import access
from db import actor_name, audit, exec_, q, q1
from mari_components.knowledge.approvals import (
    ApprovalPolicy as ComponentApprovalPolicy,
    ReviewItem as ComponentReviewItem,
    evaluate_approval,
)

POLICY_VERSION = "review-v1"


@dataclass(frozen=True)
class ReviewRecord:
    id: str
    kind: str
    title: str
    status: str
    source: str = ""
    assignee: str = ""
    due: str = ""
    subject_type: str = ""
    subject_id: str = ""
    subject_title: str = ""
    subject_href: str = ""
    proposer: str = ""
    confidence: float = 0.0
    evidence_count: int = 0
    trusted_source: bool = False


@dataclass(frozen=True)
class PolicyResult:
    review_id: str
    outcome: str  # allow|deny|manual
    explanation: str
    policy_version: str = POLICY_VERSION
    replayed: bool = False
    dry_run: bool = True


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(max(offset, 0)).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = cursor + "=" * (-len(cursor) % 4)
        return max(0, int(base64.urlsafe_b64decode(raw).decode()))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid Review cursor") from None


def _record(row: dict) -> ReviewRecord:
    return ReviewRecord(
        id=row["review_id"], kind=row["kind"], title=row["title"], status=row["status"],
        source=row.get("source") or "", assignee=row.get("assignee") or "",
        due=row["due"].isoformat() if hasattr(row.get("due"), "isoformat") else (row.get("due") or ""),
        subject_type=row.get("subject_type") or "", subject_id=str(row.get("subject_id") or ""),
        subject_title=row.get("subject_title") or "", subject_href=row.get("subject_href") or "",
        proposer=row.get("proposer") or "", confidence=float(row.get("confidence") or 0),
        evidence_count=int(row.get("evidence_count") or 0),
        trusted_source=bool(row.get("trusted_source")),
    )


def project_items() -> list[ReviewRecord]:
    """Read every actionable source into one non-destructive projection."""
    project_id = access.require_current_access().project_id
    rows = q("""
      WITH projected AS (
      SELECT 'task:' || t.id AS review_id, t.kind, t.title,
             CASE WHEN t.done THEN 'done' ELSE 'pending' END AS status,
             '' AS source, t.assignee, t.due_date AS due,
             t.subject_type, t.subject_id, t.subject_title, t.subject_href,
             t.assignee AS proposer, 0::real AS confidence, 0 AS evidence_count, false AS trusted_source
        FROM tasks t WHERE t.project_id = %s
      UNION ALL
      SELECT 'fact:' || f.id, 'fact', f.claim, 'pending', f.source, f.owner_name, NULL,
             'fact', f.id::text, f.claim, '/facts?fact=' || f.id, f.owner_name,
             0::real, CASE WHEN f.document_id IS NULL THEN 0 ELSE 1 END, false
        FROM facts f WHERE f.project_id = %s AND lower(f.status) NOT IN ('verified', 'rejected')
      UNION ALL
      SELECT 'decision:' || d.id, 'decision', d.statement, 'pending', d.source_label,
             coalesce(d.owners[1], ''), NULL, 'decision', d.id::text, d.statement,
             '/decisions?decision=' || d.id, coalesce(d.owners[1], ''), 0::real,
             CASE WHEN d.source_label <> '' THEN 1 ELSE 0 END, false
        FROM decisions d WHERE d.project_id = %s AND d.status = 'proposed'
      UNION ALL
      SELECT 'answer:' || a.id, 'answer', a.question, 'pending',
             coalesce(a.sources->0->>'source', ''), a.owner_name, NULL, 'answer', a.id::text,
             a.question, '/answers?answer=' || a.id, a.owner_name, 0::real,
             jsonb_array_length(a.sources), false
        FROM approved_answers a WHERE a.project_id = %s AND a.status = 'draft'
      UNION ALL
      SELECT 'finding:' || f.id, 'finding', f.text, 'pending', d.source, '', NULL,
             'document', d.id::text, d.title,
             '/knowledge/doc?id=' || d.id || '&pane=findings', '', 0::real, 1, false
        FROM findings f JOIN documents d ON d.id = f.document_id
        WHERE d.project_id = %s
      UNION ALL
      SELECT 'change:' || c.id, 'change', coalesce(nullif(c.reason, ''), c.replacement),
             c.status, d.source, '', NULL, 'document', d.id::text, d.title,
             '/knowledge/doc?id=' || d.id || '&tab=changes', '', 1::real, 1, false
        FROM changes c JOIN documents d ON d.id = c.document_id
        WHERE d.project_id = %s AND c.status = 'pending'
      UNION ALL
      SELECT 'workflow:' || r.id, 'workflow', w.name || ' approval', 'waiting',
             'automation', '', NULL, 'workflow', r.id::text, w.name,
             '/flows?run=' || r.id, '', 1::real, 1, true
        FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id
        WHERE r.project_id = %s AND w.project_id = r.project_id AND r.status = 'waiting'
      )
      SELECT p.review_id, p.kind, p.title, p.status, p.source, p.assignee, p.due,
             p.subject_type, p.subject_id, p.subject_title, p.subject_href,
             coalesce(nullif(s.proposer, ''), p.proposer) AS proposer,
             coalesce(s.confidence, p.confidence) AS confidence,
             coalesce(s.evidence_count, p.evidence_count) AS evidence_count,
             coalesce(s.trusted_source, p.trusted_source) AS trusted_source
        FROM projected p LEFT JOIN review_signals s ON s.review_id = p.review_id
    """, (project_id,) * 7)
    return [_record(row) for row in rows]


def filter_items(items: Iterable[ReviewRecord], *, kinds: list[str] | None = None,
                 statuses: list[str] | None = None, sources: list[str] | None = None,
                 assignees: list[str] | None = None, due: str | None = None) -> list[ReviewRecord]:
    sets = [set(x or []) for x in (kinds, statuses, sources, assignees)]
    due = (due or "").lower()
    today = dt.date.today().isoformat()
    out = []
    for item in items:
        if sets[0] and item.kind not in sets[0]: continue
        if sets[1] and item.status not in sets[1]: continue
        if sets[2] and item.source not in sets[2]: continue
        if sets[3] and item.assignee not in sets[3]: continue
        if due == "overdue" and (not item.due or item.due >= today): continue
        if due == "dated" and not item.due: continue
        if due == "undated" and item.due: continue
        out.append(item)
    return sorted(out, key=lambda x: (x.status != "pending", x.due or "9999-12-31", x.kind, x.id))


def evaluate_policy(item: ReviewRecord, reviewer: str, *, min_confidence: float = .9,
                    min_evidence: int = 2) -> PolicyResult:
    decision = evaluate_approval(
        ComponentReviewItem(
            item.id, item.kind, item.proposer.casefold(), item.confidence,
            item.evidence_count, item.trusted_source,
        ),
        reviewer.casefold(),
        ComponentApprovalPolicy(
            minimum_confidence=min_confidence,
            minimum_evidence=min_evidence,
            version=POLICY_VERSION,
        ),
    )
    return PolicyResult(item.id, decision.outcome, decision.explanation,
                        policy_version=decision.policy_version)


def _apply(item: ReviewRecord) -> None:
    kind, raw_id = item.id.split(":", 1)
    ident = int(raw_id)
    statements = {
        "fact": ("UPDATE facts SET status = 'Verified', verified_at = current_date WHERE id = %s",),
        "decision": ("UPDATE decisions SET status = 'ratified', decided_on = current_date WHERE id = %s",),
        "answer": ("UPDATE approved_answers SET status = 'approved', updated = current_date WHERE id = %s",),
    }
    if kind not in statements:
        raise ValueError(f"Review kind {kind!r} cannot be automatically approved")
    exec_(statements[kind][0], (ident,))


def decide(item: ReviewRecord, reviewer: str, *, dry_run: bool = True,
           permission: Callable[[str, ReviewRecord], bool] = lambda _actor, _item: True,
           audit_hook: Callable[..., None] = audit) -> PolicyResult:
    if not permission(reviewer, item):
        return PolicyResult(item.id, "deny", "Reviewer lacks permission for this item.", dry_run=dry_run)
    result = evaluate_policy(item, reviewer)
    result = PolicyResult(**{**result.__dict__, "dry_run": dry_run})
    if dry_run:
        return result
    fingerprint = hashlib.sha256(json.dumps(item.__dict__, sort_keys=True).encode()).hexdigest()
    existing = q1("""SELECT outcome, explanation FROM review_decisions
                     WHERE review_id = %s AND policy_version = %s AND subject_fingerprint = %s""",
                  (item.id, POLICY_VERSION, fingerprint))
    if existing:
        return PolicyResult(item.id, existing["outcome"], existing["explanation"],
                            replayed=True, dry_run=False)
    exec_("""INSERT INTO review_decisions
             (review_id, policy_version, outcome, explanation, reviewer, subject_fingerprint)
             VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
          (item.id, POLICY_VERSION, result.outcome, result.explanation, reviewer, fingerprint))
    if result.outcome == "allow":
        _apply(item)
    audit_hook("evaluated review policy", item.title, actor=reviewer,
               detail=[("Review id", item.id), ("Outcome", result.outcome),
                       ("Policy", POLICY_VERSION), ("Explanation", result.explanation)])
    return result


def find_item(review_id: str) -> ReviewRecord | None:
    return next((item for item in project_items() if item.id == review_id), None)

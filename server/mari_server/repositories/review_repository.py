"""Postgres projection and persistence adapters for Unified Review."""

from __future__ import annotations

from mari_server.domain import access
from mari_server.repositories.database import audit, exec_, q, q1

from mari_server.services.review import ReviewPorts
from mari_server.domain.review import POLICY_VERSION, PolicyResult, ReviewRecord


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


def find_item(review_id: str) -> ReviewRecord | None:
    return next((item for item in project_items() if item.id == review_id), None)


def _existing(review_id: str, policy_version: str, fingerprint: str):
    row = q1(
        """SELECT outcome, explanation FROM review_decisions
             WHERE review_id = %s AND policy_version = %s AND subject_fingerprint = %s""",
        (review_id, policy_version, fingerprint),
    )
    return (row["outcome"], row["explanation"]) if row else None


def _record_decision(item: ReviewRecord, result: PolicyResult, reviewer: str, fingerprint: str) -> None:
    exec_(
        """INSERT INTO review_decisions
             (review_id, policy_version, outcome, explanation, reviewer, subject_fingerprint)
             VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (item.id, POLICY_VERSION, result.outcome, result.explanation, reviewer, fingerprint),
    )


def _apply_approval(item: ReviewRecord) -> None:
    kind, raw_id = item.id.split(":", 1)
    statements = {
        "fact": "UPDATE facts SET status = 'Verified', verified_at = current_date WHERE id = %s",
        "decision": "UPDATE decisions SET status = 'ratified', decided_on = current_date WHERE id = %s",
        "answer": "UPDATE approved_answers SET status = 'approved', updated = current_date WHERE id = %s",
    }
    if kind not in statements:
        raise ValueError(f"Review kind {kind!r} cannot be automatically approved")
    exec_(statements[kind], (int(raw_id),))


def _audit_decision(item: ReviewRecord, result: PolicyResult, reviewer: str) -> None:
    audit(
        "evaluated review policy", item.title, actor=reviewer,
        detail=[("Review id", item.id), ("Outcome", result.outcome),
                ("Policy", POLICY_VERSION), ("Explanation", result.explanation)],
    )


def ports(*, audit_hook=None) -> ReviewPorts:
    return ReviewPorts(
        existing_decision=_existing,
        record_decision=_record_decision,
        apply_approval=_apply_approval,
        audit_decision=audit_hook or _audit_decision,
    )

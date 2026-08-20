from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import queries
from mari_server.application import review
from mari_server.domain.review import ReviewRecord
from mari_server.infrastructure import review_repository


def item(**overrides) -> ReviewRecord:
    values = dict(id="fact:1", kind="fact", title="Retention is 30 days",
                  status="pending", source="handbook", assignee="Dana",
                  subject_type="fact", subject_id="1", subject_title="Retention",
                  subject_href="/facts?fact=1", proposer="Alex", confidence=.96,
                  evidence_count=3, trusted_source=True)
    values.update(overrides)
    return ReviewRecord(**values)


def ports(*, existing=None, writes=None, auditor=None):
    writes = writes if writes is not None else []
    return review.ReviewPorts(
        existing_decision=lambda *_args: existing,
        record_decision=lambda candidate, result, reviewer, fingerprint: writes.append(
            ("record", candidate.id, result.outcome, reviewer, fingerprint),
        ),
        apply_approval=lambda candidate: writes.append(("apply", candidate.id)),
        audit_decision=lambda candidate, result, reviewer: (
            auditor(candidate, result, reviewer) if auditor else None
        ),
    )


class ReviewProjectionTests(unittest.TestCase):
    def test_projection_query_unifies_every_review_kind(self) -> None:
        rows = [{"review_id": f"{kind}:{n}", "kind": kind, "title": kind,
                 "status": "pending", "subject_id": str(n)}
                for n, kind in enumerate(
                    ("task", "fact", "decision", "answer", "finding", "change", "workflow"), 1)]
        with patch.object(review_repository.access, "require_current_access", return_value=SimpleNamespace(project_id=7)), \
             patch.object(review_repository, "q", return_value=rows) as query:
            projected = review_repository.project_items()
        self.assertEqual(query.call_args.args[1], (7,) * 7)
        self.assertEqual({x.kind for x in projected},
                         {"task", "fact", "decision", "answer", "finding", "change", "workflow"})
        sql = query.call_args.args[0]
        for table in ("tasks", "facts", "decisions", "approved_answers", "findings",
                      "changes", "workflow_runs"):
            self.assertIn(table, sql)

    def test_filters_sort_and_cursor_pagination_are_bounded(self) -> None:
        rows = [item(id=f"fact:{n}", source="trusted" if n % 2 else "other",
                     assignee="Dana" if n % 3 else "Lee") for n in range(140)]
        with patch.object(review_repository, "project_items", return_value=rows):
            page = queries.Query().review_items(first=500, sources=["trusted"], assignees=["Dana"])
        self.assertLessEqual(len(page.items), 100)
        self.assertEqual(page.total_count, len([x for x in rows if x.source == "trusted" and x.assignee == "Dana"]))
        self.assertEqual(review.decode_cursor(page.page_info.end_cursor), len(page.items))

    def test_kind_status_source_assignee_and_due_filters_compose(self) -> None:
        rows = [item(id="fact:1", due="2020-01-01"),
                item(id="decision:2", kind="decision", due=""),
                item(id="fact:3", status="done", due="2020-01-01")]
        filtered = review.filter_items(rows, kinds=["fact"], statuses=["pending"],
                                       sources=["handbook"], assignees=["Dana"], due="overdue")
        self.assertEqual([x.id for x in filtered], ["fact:1"])

    def test_invalid_cursor_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid Review cursor"):
            review.decode_cursor("not-a-cursor")


class ApprovalPolicyTests(unittest.TestCase):
    def test_policy_allows_only_thresholded_trusted_supported_kinds(self) -> None:
        self.assertEqual(review.evaluate_policy(item(), "Dana").outcome, "allow")
        self.assertEqual(review.evaluate_policy(item(confidence=.89), "Dana").outcome, "manual")
        self.assertEqual(review.evaluate_policy(item(evidence_count=1), "Dana").outcome, "manual")
        self.assertEqual(review.evaluate_policy(item(trusted_source=False), "Dana").outcome, "manual")
        self.assertEqual(review.evaluate_policy(item(kind="workflow", id="workflow:1"), "Dana").outcome,
                         "manual")

    def test_separation_of_duties_and_permission_hook_deny(self) -> None:
        self.assertEqual(review.evaluate_policy(item(proposer="Dana"), "dana").outcome, "deny")
        denied = review.decide(item(), "Dana", ports(), permission=lambda _a, _i: False)
        self.assertEqual(denied.outcome, "deny")
        self.assertIn("permission", denied.explanation)

    def test_dry_run_has_no_write_or_audit(self) -> None:
        writes = []
        result = review.decide(item(), "Dana", ports(writes=writes), dry_run=True)
        self.assertEqual(result.outcome, "allow")
        self.assertEqual(writes, [])

    def test_real_decision_is_recorded_applied_audited_and_replayed(self) -> None:
        writes = []
        auditor = Mock()
        first = review.decide(item(), "Dana", ports(writes=writes, auditor=auditor), dry_run=False)
        replay = review.decide(
            item(), "Dana", ports(existing=("allow", "same"), writes=writes, auditor=auditor),
            dry_run=False,
        )
        self.assertEqual(first.outcome, "allow")
        self.assertTrue(any(write[0] == "record" for write in writes))
        self.assertIn(("apply", "fact:1"), writes)
        self.assertTrue(replay.replayed)
        self.assertEqual(auditor.call_count, 1)


if __name__ == "__main__":
    unittest.main()

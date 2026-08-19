from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import queries
import review


def item(**overrides) -> review.ReviewRecord:
    values = dict(id="fact:1", kind="fact", title="Retention is 30 days",
                  status="pending", source="handbook", assignee="Dana",
                  subject_type="fact", subject_id="1", subject_title="Retention",
                  subject_href="/facts?fact=1", proposer="Alex", confidence=.96,
                  evidence_count=3, trusted_source=True)
    values.update(overrides)
    return review.ReviewRecord(**values)


class ReviewProjectionTests(unittest.TestCase):
    def test_projection_query_unifies_every_review_kind(self) -> None:
        rows = [{"review_id": f"{kind}:{n}", "kind": kind, "title": kind,
                 "status": "pending", "subject_id": str(n)}
                for n, kind in enumerate(
                    ("task", "fact", "decision", "answer", "finding", "change", "workflow"), 1)]
        with patch.object(review.access, "require_current_access", return_value=SimpleNamespace(project_id=7)), \
             patch.object(review, "q", return_value=rows) as query:
            projected = review.project_items()
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
        with patch.object(review, "project_items", return_value=rows):
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
        denied = review.decide(item(), "Dana", permission=lambda _a, _i: False)
        self.assertEqual(denied.outcome, "deny")
        self.assertIn("permission", denied.explanation)

    def test_dry_run_has_no_write_or_audit(self) -> None:
        with patch.object(review, "exec_") as write:
            result = review.decide(item(), "Dana", dry_run=True, audit_hook=Mock())
        self.assertEqual(result.outcome, "allow")
        write.assert_not_called()

    def test_real_decision_is_recorded_applied_audited_and_replayed(self) -> None:
        writes: list[tuple[str, tuple]] = []
        auditor = Mock()
        with patch.object(review, "q1", side_effect=[None, {
                "outcome": "allow", "explanation": "same"}]), \
             patch.object(review, "exec_", side_effect=lambda sql, args=(): writes.append((sql, args))):
            first = review.decide(item(), "Dana", dry_run=False, audit_hook=auditor)
            replay = review.decide(item(), "Dana", dry_run=False, audit_hook=auditor)
        self.assertEqual(first.outcome, "allow")
        self.assertTrue(any("INSERT INTO review_decisions" in sql for sql, _ in writes))
        self.assertTrue(any("UPDATE facts SET status = 'Verified'" in sql for sql, _ in writes))
        self.assertTrue(replay.replayed)
        self.assertEqual(auditor.call_count, 1)


if __name__ == "__main__":
    unittest.main()

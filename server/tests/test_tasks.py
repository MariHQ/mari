from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

import mutations_knowledge
import queries
import access


PROJECT = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)


class TaskSubjectTests(unittest.TestCase):
    def test_create_task_persists_optional_typed_subject(self) -> None:
        writes: list[tuple[str, tuple]] = []
        with access.use_access(PROJECT), \
             patch.object(mutations_knowledge, "exec_", side_effect=lambda sql, args=(): writes.append((sql, args))), \
             patch.object(mutations_knowledge, "audit") as audit:
            result = mutations_knowledge.MutKnowledge().create_task(
                "Review retention claim", assignee="Dana Rodriguez", due="2026-08-25",
                subject_type="fact", subject_id="42", subject_title="Retention is 30 days",
                subject_href="/facts?fact=42",
            )

        self.assertTrue(result)
        self.assertIn("subject_type, subject_id, subject_title, subject_href", writes[0][0])
        self.assertEqual(writes[0][1], (
            7, "Review retention claim", "Dana Rodriguez", "DR", "factcheck", "Fact check",
            "2026-08-25", "fact", "42", "Retention is 30 days", "/facts?fact=42",
        ))
        self.assertIn(("Subject type", "fact"), audit.call_args.kwargs["detail"])

    def test_create_task_keeps_legacy_callers_subjectless(self) -> None:
        writes: list[tuple[str, tuple]] = []
        with access.use_access(PROJECT), \
             patch.object(mutations_knowledge, "exec_", side_effect=lambda sql, args=(): writes.append((sql, args))), \
             patch.object(mutations_knowledge, "audit"):
            mutations_knowledge.MutKnowledge().create_task("Legacy review")

        self.assertEqual(writes[0][1][-4:], ("", "", "", ""))

    def test_tasks_query_exposes_subject_reference(self) -> None:
        row = {
            "id": 7, "title": "Review retention claim", "assignee_initials": "DR",
            "assignee_tint": 1, "kind": "factcheck", "kind_label": "Fact check",
            "done": False, "due_date": dt.date(2026, 8, 25), "overdue": False,
            "subject_type": "fact", "subject_id": "42",
            "subject_title": "Retention is 30 days", "subject_href": "/facts?fact=42",
        }
        with access.use_access(PROJECT), patch.object(queries, "q", return_value=[row]):
            task = queries.Query().tasks()[0]

        self.assertEqual(task.due, "2026-08-25")
        self.assertEqual((task.subject_type, task.subject_id, task.subject_title, task.subject_href),
                         ("fact", "42", "Retention is 30 days", "/facts?fact=42"))


if __name__ == "__main__":
    unittest.main()

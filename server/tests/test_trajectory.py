from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from mari_server.persistence.postgres import trajectories as trajectory
from mari_server.identity import access


class TrajectoryHarvestTests(unittest.TestCase):
    def test_normalization_redacts_content_and_secrets(self):
        steps = trajectory.normalize_steps([{
            "name": "tag_document",
            "args": {"id": 4, "new_body": "private", "token": "secret", "note": "fix typo"},
            "summary": "updated doc",
            "ok": True,
        }])
        self.assertEqual(steps[0]["args"], {"id": 4, "note": "fix typo"})
        self.assertNotIn("private", json.dumps(steps))
        self.assertEqual(steps[0]["action_family"], "change")

    def test_hierarchy_marks_failure_recovery_and_rework(self):
        steps = trajectory.normalize_steps([
            {"name": "search", "args": {"query": "auth"}, "summary": "3 hits", "ok": True},
            {"name": "tag_document", "args": {"id": 4}, "summary": "blocked", "ok": False},
            {"name": "read_document", "args": {"id": 4}, "summary": "read", "ok": True},
            {"name": "tag_document", "args": {"id": 4}, "summary": "updated", "ok": True},
        ])
        phases = trajectory.segment_phases(steps)
        self.assertEqual([p["family"] for p in phases], ["discover", "change", "inspect", "change"])
        self.assertEqual(sum(p["failures"] for p in phases), 1)
        # One repeated edit signature plus one change -> inspect -> change loop.
        self.assertEqual(trajectory.rework_count(steps), 2)

    def test_analysis_uses_progressive_llm_layers_and_taxonomy(self):
        updates = []
        answers = iter([
            {"workflow": "Searched, inspected a page, then updated it."},
            {"activity": "Updated documentation from retrieved evidence."},
            {"category": "Documentation maintenance"},
            {"intent": "Repair product documentation"},
        ])
        steps = trajectory.normalize_steps([
            {"name": "search", "args": {"query": "auth"}, "summary": "3 hits", "ok": True},
            {"name": "read_document", "args": {"id": 4}, "summary": "read", "ok": True},
            {"name": "tag_document", "args": {"id": 4}, "summary": "updated", "ok": True},
        ])
        project = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)
        with access.use_access(project), \
             patch.object(trajectory.llm, "generate_json", side_effect=lambda _: next(answers)), \
             patch.object(trajectory, "q", return_value=[{"category": "Incident response"}]), \
             patch.object(trajectory, "exec_", side_effect=lambda sql, args=(): updates.append((sql, args))):
            trajectory.analyze(9, "Fix the auth documentation", steps)
        self.assertEqual(updates[0][1][2], "Documentation maintenance")
        self.assertEqual(updates[0][1][3], "Repair product documentation")
        self.assertIn("Searched", updates[0][1][0])


if __name__ == "__main__":
    unittest.main()

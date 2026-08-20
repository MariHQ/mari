from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import flowengine


class WorkflowStepTests(unittest.TestCase):
    def test_idempotent_step_retries_once_and_records_recovery(self) -> None:
        impl = Mock(side_effect=[ConnectionError("temporary"), ("passed", "done", {"facts": 2})])
        with patch.object(flowengine.time, "sleep"):
            status, detail, updates = flowengine._run_step("scan_facts", impl, {}, {})
        self.assertEqual(status, "passed")
        self.assertIn("succeeded on retry", detail)
        self.assertEqual(updates, {"facts": 2})
        self.assertEqual(impl.call_count, 2)

    def test_non_idempotent_deploy_is_never_retried(self) -> None:
        impl = Mock(side_effect=RuntimeError("upload uncertain"))
        status, detail, _ = flowengine._run_step("deploy_site", impl, {}, {})
        self.assertEqual(status, "failed")
        self.assertIn("upload uncertain", detail)
        impl.assert_called_once()

    def test_condition_and_approval_preserve_dry_run_semantics(self) -> None:
        self.assertTrue(flowengine._step_condition({"field": "facts", "greater_than": 1}, {"facts": 2})[2]["branch_taken"])
        status, detail, updates = flowengine._step_approval({"assignee": "Reviewer"}, {})
        self.assertEqual((status, updates), ("waiting", {"pause": True}))
        self.assertIn("Reviewer", detail)
        self.assertEqual(flowengine._step_approval({}, {"dry_run": True})[0], "failed")

    def test_document_trigger_filters_are_anded(self) -> None:
        docs = [
            {"id": 1, "source_id": 4, "source_path": "docs/runbook.md"},
            {"id": 2, "source_id": 4, "source_path": "src/app.py"},
            {"id": 3, "source_id": 5, "source_path": "docs/other.md"},
        ]
        trig = {"on": "document_changed", "source_id": 4, "tag": "review", "path_glob": "docs/**"}
        matched = flowengine._trigger_matches(trig, "document_changed", docs, {1: {"review"}, 2: {"review"}, 3: {"review"}})
        self.assertEqual([d["id"] for d in matched], [1])
        self.assertEqual(flowengine._trigger_matches(trig, "document_added", docs, {1: {"review"}}), [])

    def test_bounded_work_queue_receives_run_without_spawning_raw_thread(self) -> None:
        pool = Mock()
        with patch.object(flowengine, "_run_pool", pool):
            flowengine.start_run(91, resume_from=3)
        pool.submit.assert_called_once_with(flowengine._guarded_run, 91, 3)
        self.assertGreaterEqual(flowengine.FLOW_WORKERS, 1)

    def test_scan_steps_use_document_ids_selected_by_fetch_step(self) -> None:
        with patch("mutations_knowledge.scan_facts_for", return_value=(3, 2, "")) as scan:
            status, _, updates = flowengine._step_scan_facts({}, {"doc_ids": [7, 8]})
        self.assertEqual(status, "passed")
        self.assertEqual(updates["facts"], 3)
        scan.assert_called_once_with([7, 8])

    def test_scheduler_has_orderly_shutdown_and_can_restart(self) -> None:
        flowengine.stop_scheduler(timeout=0)
        with patch.object(flowengine, "reconcile_stale_runs"):
            flowengine.start_scheduler()
        first = flowengine._SCHEDULER["thread"]
        self.assertTrue(first.is_alive())
        flowengine.stop_scheduler(timeout=1)
        self.assertFalse(first.is_alive())
        self.assertFalse(flowengine._SCHEDULER["started"])
        with patch.object(flowengine, "reconcile_stale_runs"):
            flowengine.start_scheduler()
        self.assertIsNot(first, flowengine._SCHEDULER["thread"])
        flowengine.stop_scheduler(timeout=1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from mari_server.automations import runtime as flowengine
from mari_server.identity import context as access


class WorkflowStepTests(unittest.TestCase):
    def test_idempotent_step_retries_once_and_records_recovery(self) -> None:
        impl = Mock(side_effect=[ConnectionError("temporary"), ("passed", "done", {"facts": 2})])
        with patch.object(flowengine.time, "sleep"):
            status, detail, updates = flowengine._run_step("scan_facts", impl, {}, {})
        self.assertEqual(status, "passed")
        self.assertIn("succeeded on retry", detail)
        self.assertEqual(updates, {"facts": 2})
        self.assertEqual(impl.call_count, 2)

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
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        with access.use_access(context), patch.object(flowengine, "_run_pool", pool):
            flowengine.start_run(91, resume_from=3)
        pool.submit.assert_called_once_with(flowengine._guarded_run, 91, 3, context)
        self.assertGreaterEqual(flowengine.FLOW_WORKERS, 1)

    def test_scan_steps_use_document_ids_selected_by_fetch_step(self) -> None:
        from mari_server.knowledge import service
        with patch.object(service, "scan_facts_for", return_value=(3, 2, "")) as scan:
            status, _, updates = flowengine._step_scan_facts({}, {"doc_ids": [7, 8]})
        self.assertEqual(status, "passed")
        self.assertEqual(updates["facts"], 3)
        scan.assert_called_once_with([7, 8], claims_per_document=2)

    def test_fact_extraction_is_registered_hourly_for_each_project(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "seed")
        with access.use_access(context), \
             patch.object(flowengine.workflow_store, "find_by_step", return_value=None), \
             patch.object(flowengine.workflow_store, "create_default_workflow", return_value=17) as create:
            self.assertEqual(flowengine.ensure_fact_scan_flow(), 17)
        self.assertEqual(create.call_args.kwargs["trigger"], {"on": "schedule", "every_minutes": 60})
        self.assertEqual(create.call_args.kwargs["nodes"][1]["config"], {"k": 50, "rotate": "facts"})

    def test_fact_scan_configuration_is_bounded_and_persisted_on_the_workflow(self) -> None:
        nodes = [
            {"kind": "trigger", "config": {}},
            {"kind": "fetch_docs", "label": "old", "config": {}},
            {"kind": "scan_facts", "config": {}},
        ]
        with patch.object(flowengine.workflow_store, "workflow_nodes", return_value=nodes), \
             patch.object(flowengine.workflow_store, "update_nodes") as update, \
             patch.object(flowengine.workflow_store, "set_trigger") as trigger:
            result = flowengine.configure_fact_scan_flow(17, {
                "limit": 999, "claims_per_document": 99,
                "source_ids": [8, 8, 3], "query": " platform ",
                "tag": " canonical ", "schedule_minutes": 360,
            })
        self.assertEqual(result["k"], 200)
        self.assertEqual(result["claims_per_document"], 10)
        self.assertEqual(result["source_ids"], [3, 8])
        saved = update.call_args.args[1]
        self.assertEqual(saved[1]["config"]["query"], "platform")
        self.assertEqual(saved[2]["config"], {"claims_per_document": 10})
        trigger.assert_called_once_with(17, {"on": "schedule", "every_minutes": 360})

    def test_fact_scan_configuration_rejects_unknown_schedule(self) -> None:
        with self.assertRaisesRegex(ValueError, "schedule"):
            flowengine.configure_fact_scan_flow(17, {"schedule_minutes": 17})

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

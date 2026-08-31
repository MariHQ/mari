from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from mari_server.automations import runtime as flowengine
from mari_server.identity import context as access


class WorkflowStepTests(unittest.TestCase):
    def test_sync_flow_is_keyed_by_source_inside_the_current_project(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "seed")
        existing = {"id": 12, "nodes": [{"kind": "sync_source", "config": {"source_id": 7}}]}
        with access.use_access(context), \
             patch.object(flowengine.workflow_store, "find_by_step", return_value=existing) as find, \
             patch.object(flowengine.workflow_store, "create_default_workflow") as create:
            self.assertIsNone(flowengine.ensure_sync_flow(7, "Slack · test"))
        find.assert_called_once_with("sync_source", config={"source_id": 7})
        create.assert_not_called()

    def test_sync_flow_creation_is_project_scoped(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "seed")
        with access.use_access(context), \
             patch.object(flowengine.workflow_store, "find_by_step", return_value=None), \
             patch.object(flowengine.workflow_store, "create_default_workflow", return_value=18) as create:
            self.assertEqual(flowengine.ensure_sync_flow(7, "Slack · test"), 18)
        self.assertNotIn("project_scoped", create.call_args.kwargs)
        self.assertEqual(create.call_args.kwargs["nodes"][1]["config"]["source_id"], 7)

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
        candidates = [{"claim": "A", "document_id": 7}]
        with patch.object(service, "extract_fact_candidates_for", return_value=(candidates, 2, "")) as scan, \
             patch("mari_server.persistence.postgres.knowledge.stage_fact_candidates", return_value=1) as stage:
            status, _, updates = flowengine._step_scan_facts({}, {"doc_ids": [7, 8], "run_id": 91})
        self.assertEqual(status, "passed")
        self.assertEqual(updates["facts"], 1)
        scan.assert_called_once_with(
            [7, 8], claims_per_document=2, instructions="", run_id=91,
            max_llm_calls=50, max_input_tokens=100000, max_output_tokens=20000,
        )
        stage.assert_called_once_with(91, candidates)

    def test_fact_extraction_is_registered_hourly_for_each_project(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "seed")
        with access.use_access(context), \
             patch.object(flowengine.workflow_store, "find_by_step", return_value=None), \
             patch.object(flowengine.workflow_store, "create_default_workflow", return_value=17) as create:
            self.assertEqual(flowengine.ensure_fact_scan_flow(), 17)
        self.assertEqual(create.call_args.kwargs["trigger"], {"on": "schedule", "every_minutes": 60})
        nodes = create.call_args.kwargs["nodes"]
        self.assertEqual(nodes[1]["config"], {"k": 50, "rotate": "facts"})
        self.assertEqual(nodes[4]["config"]["mode"], "llm")
        self.assertEqual(nodes[4]["config"]["max_calls"], 50)
        self.assertEqual(nodes[6]["config"], {"mode": "ai", "minimum_confidence": .85})

    def test_fact_scan_configuration_is_bounded_and_persisted_on_the_workflow(self) -> None:
        nodes = [
            {"kind": "trigger", "config": {}},
            {"kind": "fetch_docs", "label": "old", "config": {}},
            {"kind": "scan_facts", "config": {}},
            {"kind": "review_facts", "config": {}},
            {"kind": "publish_facts", "config": {}},
        ]
        with patch.object(flowengine.workflow_store, "workflow_nodes", return_value=nodes), \
             patch.object(flowengine.workflow_store, "update_nodes") as update, \
             patch.object(flowengine.workflow_store, "set_trigger") as trigger:
            result = flowengine.configure_fact_scan_flow(17, {
                "limit": 999, "claims_per_document": 99,
                "source_ids": [8, 8, 3], "query": " platform ",
                "tag": " canonical ", "schedule_minutes": 360,
                "review_mode": "ai", "review_instructions": "Only durable limits.",
                "publish_status": "verified",
            })
        self.assertEqual(result["k"], 200)
        self.assertEqual(result["claims_per_document"], 10)
        self.assertEqual(result["source_ids"], [3, 8])
        saved = update.call_args.args[1]
        self.assertEqual(saved[0], {
            "kind": "trigger", "label": "Every 6 hours",
            "config": {"label": "Scheduled · every 6 hours"},
        })
        self.assertEqual(saved[1]["config"]["query"], "platform")
        self.assertEqual(saved[2]["config"], {
            "claims_per_document": 10, "instructions": "Only durable limits.",
            "max_llm_calls": 200, "max_input_tokens": 100000,
            "max_output_tokens": 20000,
        })
        self.assertEqual(saved[3]["config"], {
            "mode": "ai", "minimum_confidence": .85,
            "instructions": "Only durable limits.",
        })
        self.assertEqual(saved[4]["config"], {"status": "verified"})
        trigger.assert_called_once_with(17, {"on": "schedule", "every_minutes": 360})

    def test_fact_scan_reconfiguration_keeps_a_tuned_review_threshold(self) -> None:
        nodes = [
            {"kind": "trigger", "config": {}},
            {"kind": "scan_facts", "config": {}},
            {"kind": "review_facts", "config": {"mode": "ai", "minimum_confidence": .6}},
            {"kind": "publish_facts", "config": {}},
        ]
        with patch.object(flowengine.workflow_store, "workflow_nodes", return_value=nodes), \
             patch.object(flowengine.workflow_store, "update_nodes") as update, \
             patch.object(flowengine.workflow_store, "set_trigger"):
            flowengine.configure_fact_scan_flow(17, {"review_mode": "ai"})
        saved = update.call_args.args[1]
        review = next(node for node in saved if node["kind"] == "review_facts")
        # the dialog has no threshold field, so a reconfigure must not reset it
        self.assertEqual(review["config"]["minimum_confidence"], .6)

    def test_fact_scan_configuration_rejects_unknown_schedule(self) -> None:
        with self.assertRaisesRegex(ValueError, "schedule"):
            flowengine.configure_fact_scan_flow(17, {"schedule_minutes": 17})

    def test_legacy_hourly_fact_name_becomes_schedule_neutral(self) -> None:
        existing = {
            "id": 17,
            "name": "Hourly fact extraction",
            "description": "Scans new and changed documents for atomic, checkable claims every hour.",
            "nodes": [],
        }
        with patch.object(flowengine.workflow_store, "find_by_step", return_value=existing), \
             patch.object(flowengine.workflow_store, "update_metadata") as update, \
             patch.object(flowengine, "_adopt_rotation"), \
             patch.object(flowengine, "_adopt_fact_review"), \
             patch.object(flowengine, "_adopt_fact_impact"), \
             patch.object(flowengine, "_adopt_fact_intelligence"), \
             patch.object(flowengine, "_normalize_fact_intelligence_config"):
            self.assertEqual(flowengine.ensure_fact_scan_flow(), 17)
        update.assert_called_once_with(
            17, "Fact extraction", flowengine.FACT_SCAN_DESCRIPTION,
        )

    def test_fact_impact_mapping_is_a_first_class_workflow_step(self) -> None:
        with patch("mari_server.knowledge.service.map_fact_candidate_impact",
                   return_value={"impact_links": 7, "high_impact_facts": 2}):
            status, detail, updates = flowengine._step_map_fact_impact({}, {"run_id": 91})
        self.assertEqual(status, "passed")
        self.assertIn("7 evidence links", detail)
        self.assertEqual(updates["high_impact_facts"], 2)

    def test_staged_fact_workflow_adopts_temporal_impact_mapping(self) -> None:
        nodes = [
            {"kind": "trigger"}, {"kind": "fetch_docs"}, {"kind": "scan_facts"},
            {"kind": "review_facts"}, {"kind": "publish_facts"},
        ]
        with patch.object(flowengine.workflow_store, "workflow_nodes", return_value=nodes), \
             patch.object(flowengine.workflow_store, "update_nodes") as update:
            flowengine._adopt_fact_impact(17)
        saved = update.call_args.args[1]
        self.assertEqual(saved[3]["kind"], "map_fact_impact")

    def test_fact_intelligence_defaults_fill_missing_bounds_without_overwriting_users(self) -> None:
        nodes = [
            {"kind": "trigger", "config": {}}, {"kind": "fetch_docs", "config": {}},
            {"kind": "scan_facts", "config": {"claims_per_document": 7}},
            {"kind": "map_fact_impact", "config": {"fact_neighbors": 3}},
            {"kind": "adjudicate_facts", "config": {"mode": "llm", "max_calls": 2}},
            {"kind": "cluster_facts", "config": {}},
            {"kind": "review_facts", "config": {"mode": "human"}},
            {"kind": "publish_facts", "config": {"status": "needs_review"}},
        ]
        with patch.object(flowengine.workflow_store, "workflow_nodes", return_value=nodes), \
             patch.object(flowengine.workflow_store, "update_nodes") as update:
            flowengine._normalize_fact_intelligence_config(17)
        saved = update.call_args.args[1]
        self.assertEqual(saved[2]["config"]["claims_per_document"], 7)
        self.assertEqual(saved[2]["config"]["max_llm_calls"], 50)
        self.assertEqual(saved[3]["config"]["fact_neighbors"], 3)
        self.assertEqual(saved[4]["config"]["mode"], "llm")
        self.assertEqual(saved[4]["config"]["max_calls"], 2)
        self.assertEqual(saved[5]["config"]["label_mode"], "off")

    def test_waiting_run_rows_survive_a_new_workflow_stage(self) -> None:
        run = {"rows_data": [
            {"step": "Trigger", "status": "passed", "detail": "manual"},
            {"step": "Review", "status": "passed", "detail": "approved"},
            {"step": "Publish", "status": "pending", "detail": ""},
        ], "stats": {"ctx": {"run_id": 9}}}
        workflow = {"nodes": [
            {"kind": "trigger", "label": "Trigger", "config": {}},
            {"kind": "map_fact_impact", "label": "Map impact", "config": {}},
            {"kind": "review_facts", "label": "Review", "config": {}},
            {"kind": "publish_facts", "label": "Publish", "config": {}},
        ]}
        persisted: list[list[dict]] = []
        with patch.object(flowengine.workflow_store, "load_run", return_value=(run, workflow)), \
             patch.object(flowengine, "_run_step", return_value=("passed", "done", {})), \
             patch.object(flowengine, "_persist", side_effect=lambda _id, rows, *_args: persisted.append([dict(row) for row in rows])):
            flowengine.execute_run(9, resume_from=3)
        self.assertEqual([row["step"] for row in persisted[-1]],
                         ["Trigger", "Map impact", "Review", "Publish"])

    def test_human_fact_review_waits_until_every_candidate_has_a_verdict(self) -> None:
        with patch("mari_server.persistence.postgres.knowledge.fact_candidate_counts",
                   return_value={"pending": 2, "accepted": 1, "rejected": 0}):
            status, detail, updates = flowengine._step_review_facts(
                {"mode": "human"}, {"run_id": 91},
            )
        self.assertEqual(status, "waiting")
        self.assertIn("2 candidates", detail)
        self.assertEqual(updates, {"pause": True})

    def test_ai_review_waits_for_humans_when_bounded_proposals_abstain(self) -> None:
        with patch("mari_server.knowledge.service.apply_ai_fact_proposals",
                   return_value={"pending": 1, "accepted": 2, "rejected": 0,
                                 "deferred": 1}):
            status, detail, updates = flowengine._step_review_facts(
                {"mode": "ai", "minimum_confidence": .9}, {"run_id": 91},
            )
        self.assertEqual(status, "waiting")
        self.assertIn("need human review", detail)
        self.assertEqual(updates["accepted_facts"], 2)

    def test_fact_publish_only_promotes_reviewed_candidates(self) -> None:
        with patch("mari_server.persistence.postgres.knowledge.publish_fact_candidates",
                   return_value=3) as publish, \
             patch("mari_server.identity.actor.actor_name", return_value="Raphael"):
            status, detail, updates = flowengine._step_publish_facts(
                {"status": "verified"}, {"run_id": 91},
            )
        self.assertEqual(status, "passed")
        self.assertIn("Verified", detail)
        self.assertEqual(updates, {"published_facts": 3})
        publish.assert_called_once_with(91, "Raphael", verified=True)

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

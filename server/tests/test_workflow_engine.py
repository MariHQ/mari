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
        flowengine._dequeue(91)
        flowengine._QUEUE_TICKER["stop"].set()

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


class _ScriptedConnection:
    """A psycopg stand-in: each execute pops the next scripted result.
    Records normalized SQL so a test can assert on the statements issued."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple[str, tuple]] = []
        self._current = None

    def execute(self, sql, args=()):
        self.calls.append((" ".join(sql.split()), args))
        self._current = self.results.pop(0) if self.results else None
        return self

    def fetchone(self):
        return self._current

    def fetchall(self):
        return self._current or []

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ScheduledFullReconcileTests(unittest.TestCase):
    """Item 1: the scheduled sync step sweeps deleted records with a periodic
    authoritative full pass, recorded on a dedicated sources column."""

    def _run(self, cfg, *, due, stats):
        with patch.object(flowengine.workflow_store, "source_name", return_value="Confluence"), \
             patch.object(flowengine.workflow_store, "full_sync_due", return_value=due) as is_due, \
             patch.object(flowengine.workflow_store, "record_full_sync") as record, \
             patch("mari_server.sources.sync.run_sync", return_value=stats) as run_sync:
            result = flowengine._step_sync_source(cfg, {})
        return result, is_due, record, run_sync

    def test_full_pass_when_the_last_reconcile_is_older_than_the_cadence(self) -> None:
        stats = {"files_changed": 0, "items_changed": 3, "embedded": 4, "skipped": 9,
                 "snapshot_complete": True}
        (status, detail, _), is_due, record, run_sync = self._run({"source_id": 7}, due=True, stats=stats)
        self.assertEqual(status, "passed")
        is_due.assert_called_once_with(7, flowengine.FULL_SYNC_EVERY_HOURS)
        run_sync.assert_called_once_with(7, full=True)
        record.assert_called_once_with(7)
        self.assertIn("full reconcile", detail)

    def test_incremental_pass_when_a_recent_full_reconcile_exists(self) -> None:
        stats = {"files_changed": 0, "items_changed": 1, "embedded": 1, "skipped": 0}
        (status, detail, _), _, record, run_sync = self._run(
            {"source_id": 7, "full_every_hours": 6}, due=False, stats=stats)
        self.assertEqual(status, "passed")
        run_sync.assert_called_once_with(7, full=False)
        record.assert_not_called()
        self.assertNotIn("full reconcile", detail)

    def test_failed_full_pass_is_not_recorded_so_it_retries_next_tick(self) -> None:
        (status, _, _), _, record, run_sync = self._run({"source_id": 7}, due=True, stats={"error": "boom"})
        self.assertEqual(status, "failed")
        run_sync.assert_called_once_with(7, full=True)
        record.assert_not_called()

    def test_throttled_full_pass_is_not_recorded_so_the_snapshot_resumes(self) -> None:
        # A provider quota ends the pass without an error key; recording it
        # as a full reconcile made the next day's polls incremental against a
        # snapshot that never finished, and each 24h retry restarted from zero.
        stats = {"files_changed": 0, "items_changed": 3, "embedded": 4, "skipped": 9,
                 "throttled": "rate limited; resumes from checkpoint"}
        (status, detail, _), _, record, run_sync = self._run({"source_id": 7}, due=True, stats=stats)
        self.assertEqual(status, "passed")
        run_sync.assert_called_once_with(7, full=True)
        record.assert_not_called()
        self.assertIn("full reconcile incomplete", detail)

    def test_incomplete_full_snapshot_is_not_recorded(self) -> None:
        stats = {"files_changed": 0, "items_changed": 3, "embedded": 4, "skipped": 9,
                 "snapshot_complete": False}
        (status, detail, _), _, record, _ = self._run({"source_id": 7}, due=True, stats=stats)
        self.assertEqual(status, "passed")
        record.assert_not_called()
        self.assertFalse(detail.endswith("· full reconcile"))
        self.assertIn("incomplete", detail)

    def test_zero_cadence_disables_the_sweep(self) -> None:
        stats = {"files_changed": 0, "items_changed": 0, "embedded": 0, "skipped": 2}
        _, is_due, record, run_sync = self._run({"source_id": 7, "full_every_hours": 0}, due=True, stats=stats)
        is_due.assert_not_called()
        run_sync.assert_called_once_with(7, full=False)
        record.assert_not_called()

    def test_seeded_sync_flow_carries_the_cadence_so_it_is_editable(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "seed")
        with access.use_access(context), \
             patch.object(flowengine.workflow_store, "find_by_step", return_value=None), \
             patch.object(flowengine.workflow_store, "create_default_workflow", return_value=18) as create:
            flowengine.ensure_sync_flow(7, "Confluence")
        self.assertEqual(create.call_args.kwargs["nodes"][1]["config"],
                         {"source_id": 7, "full_every_hours": flowengine.FULL_SYNC_EVERY_HOURS})

    def test_full_sync_bookkeeping_lives_on_its_own_sources_column(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "seed")
        conn = _ScriptedConnection([{"due": True}, None])
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            self.assertTrue(flowengine.workflow_store.full_sync_due(7, 24))
            flowengine.workflow_store.record_full_sync(7)
        due_sql, due_args = conn.calls[0]
        self.assertIn("last_full_sync_at IS NULL OR last_full_sync_at < now() - make_interval", due_sql)
        self.assertEqual(due_args, (24 * 3600.0, 3, 7))
        record_sql, record_args = conn.calls[1]
        self.assertIn("UPDATE sources SET last_full_sync_at = now()", record_sql)
        self.assertNotIn("config", record_sql)
        self.assertEqual(record_args, (3, 7))


class RunLeaseTests(unittest.TestCase):
    """Item 2: runs hold a heartbeat lease, lost leases are swept, and every
    run insert takes the same row lock and concurrency check."""

    def test_persist_stamps_the_heartbeat_on_a_live_row_only(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        conn = _ScriptedConnection([{"id": 9}])
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            flowengine._persist(9, [], "running", 10, {"ctx": {}}, flowengine.time.time())
        sql, _ = conn.calls[0]
        self.assertIn("heartbeat_at = now()", sql)
        # never over a row the sweep already failed (or one that finished)
        self.assertIn("AND status IN ('running', 'waiting') RETURNING id", sql)

    def test_a_swept_runs_persist_is_a_no_op_that_stops_the_runner(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        conn = _ScriptedConnection([None])  # UPDATE matched nothing: status is 'failed'
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            self.assertFalse(flowengine.workflow_store.save_run_progress(
                9, rows=[], status="running", progress=10, stats={}, duration="00:00:01"))
            with self.assertRaises(flowengine.RunSwept):
                flowengine._persist(9, [], "running", 10, {"ctx": {}}, flowengine.time.time())

    def test_ticker_beat_never_revives_a_swept_row(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        conn = _ScriptedConnection([None])
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            self.assertFalse(flowengine.workflow_store.touch_run_heartbeat(9))
        sql, _ = conn.calls[0]
        self.assertIn("status IN ('running', 'waiting') RETURNING id", sql)

    def test_runner_aborts_when_its_row_was_swept_mid_run(self) -> None:
        run = {"rows_data": [], "stats": {"ctx": {}}}
        workflow = {"nodes": [{"kind": "trigger", "label": "Trigger", "config": {}},
                              {"kind": "fetch_docs", "label": "Fetch", "config": {}}]}
        stop = Mock()
        # the first persist lands; the sweep flips the row before the second
        with patch.object(flowengine.workflow_store, "load_run", return_value=(run, workflow)), \
             patch.object(flowengine.workflow_store, "save_run_progress",
                          side_effect=[True, False]) as save, \
             patch.object(flowengine, "_run_step", return_value=("passed", "ok", {})) as step, \
             patch.object(flowengine, "_keep_alive", return_value=stop):
            flowengine.execute_run(9)  # returns quietly; nothing to fail twice
        self.assertEqual(step.call_count, 1)
        self.assertEqual(save.call_count, 2)
        # the aborted runner wrote no 'passed' over the sweep's 'failed'
        self.assertNotIn("passed", [call.kwargs["status"] for call in save.call_args_list])
        stop.set.assert_called_once_with()

    def test_approve_restamps_the_heartbeat_when_it_resumes_a_waiting_run(self) -> None:
        waiting = {"id": 9, "number": 100009, "status": "waiting",
                   "stats": {"paused_at": 1}, "rows_data": [{"step": "a"}, {"step": "b"}]}
        conn = _ScriptedConnection([waiting, {"n": 0}, None])
        with patch.object(flowengine.workflow_store, "transaction", side_effect=lambda fn: fn(conn)):
            self.assertEqual(flowengine.workflow_store._approve(3, 9, "Eric"), (100009, 2))
        sql, args = conn.calls[2]
        self.assertIn("status = 'running', heartbeat_at = now()", sql)
        self.assertEqual(args[1:], (3, 9))

    def test_every_run_insert_stamps_a_fresh_heartbeat(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        workflow = {"id": 1, "project_id": 3, "name": "A"}
        inserts = []
        conn = _ScriptedConnection([{"?column?": 1}, None, {"id": 8, "number": 8}, None])
        with patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            flowengine.workflow_store.create_scheduled_run(workflow, {"on": "schedule"}, "label")
        inserts.append(conn.calls[2][0])
        conn = _ScriptedConnection([{"?column?": 1}, None, {"id": 8, "number": 8}, None])
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            flowengine.workflow_store.create_triggered_run(workflow, [1], {"on": "document_changed"}, "n")
        inserts.append(conn.calls[2][0])
        conn = _ScriptedConnection([{"?column?": 1}, None, {"id": 8, "number": 8}])
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            flowengine.workflow_store.create_run(1)
        inserts.append(conn.calls[2][0])
        conn = _ScriptedConnection([{"name": "A"}, None, {"id": 8, "number": 8}])
        with patch.object(flowengine.workflow_store, "transaction", side_effect=lambda fn: fn(conn)):
            flowengine.workflow_store._create_run(3, 1, False)
        inserts.append(conn.calls[2][0])
        for sql in inserts:
            self.assertIn("INSERT INTO workflow_runs", sql)
            self.assertIn("heartbeat_at", sql.split("VALUES")[0])
            self.assertIn("now())", sql.split("VALUES")[1])

    def test_a_queued_run_keeps_its_lease_until_a_worker_picks_it_up(self) -> None:
        import threading
        beat = threading.Event()
        pool = Mock()  # never runs what it is handed: every worker is busy
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        with patch.object(flowengine, "HEARTBEAT_SECONDS", 0.01), \
             patch.object(flowengine, "_run_pool", pool), \
             patch.object(flowengine.workflow_store, "touch_run_heartbeat",
                          side_effect=lambda _id: beat.set()) as touch:
            with access.use_access(context):
                flowengine.start_run(77)
            try:
                self.assertTrue(beat.wait(2))
                touch.assert_called_with(77)
                self.assertIn(77, flowengine._QUEUED)
                # a worker starting the run hands the lease to execute_run's own ticker
                with patch.object(flowengine, "execute_run"):
                    flowengine._guarded_run(77, 0, context)
                self.assertNotIn(77, flowengine._QUEUED)
            finally:
                flowengine._dequeue(77)
                flowengine._QUEUE_TICKER["stop"].set()

    def test_run_keeps_a_ticker_alive_for_its_whole_execution(self) -> None:
        run = {"rows_data": [], "stats": {"ctx": {}}}
        workflow = {"nodes": [{"kind": "trigger", "label": "Trigger", "config": {}}]}
        stop = Mock()
        with patch.object(flowengine.workflow_store, "load_run", return_value=(run, workflow)), \
             patch.object(flowengine, "_run_step", return_value=("passed", "ok", {})), \
             patch.object(flowengine, "_persist"), \
             patch.object(flowengine, "_keep_alive", return_value=stop) as keep_alive:
            flowengine.execute_run(9)
        keep_alive.assert_called_once_with(9, None)
        stop.set.assert_called_once_with()

    def test_ticker_stops_even_when_a_step_raises(self) -> None:
        run = {"rows_data": [], "stats": {"ctx": {}}}
        workflow = {"nodes": [{"kind": "trigger", "label": "Trigger", "config": {}}]}
        stop = Mock()
        with patch.object(flowengine.workflow_store, "load_run", return_value=(run, workflow)), \
             patch.object(flowengine, "_run_step", side_effect=RuntimeError("db blip")), \
             patch.object(flowengine, "_persist"), \
             patch.object(flowengine, "_keep_alive", return_value=stop):
            with self.assertRaises(RuntimeError):
                flowengine.execute_run(9)
        stop.set.assert_called_once_with()

    def test_ticker_touches_the_heartbeat_without_rewriting_rows(self) -> None:
        import threading
        beat = threading.Event()
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        with patch.object(flowengine, "HEARTBEAT_SECONDS", 0.01), \
             patch.object(flowengine.workflow_store, "touch_run_heartbeat",
                          side_effect=lambda _id: beat.set()) as touch:
            stop = flowengine._keep_alive(9, context)
            self.assertTrue(beat.wait(2))
            stop.set()
        touch.assert_called_with(9)

    def test_scheduler_pass_sweeps_lost_leases_and_survives_a_refused_insert(self) -> None:
        workflows = [
            {"id": 1, "project_id": 3, "name": "A", "trigger": {"on": "schedule", "every_minutes": 10}},
            {"id": 2, "project_id": 3, "name": "B", "trigger": {"on": "schedule", "every_minutes": 10}},
        ]
        with patch.object(flowengine.workflow_store, "fail_stale_runs", return_value=1) as sweep, \
             patch.object(flowengine.workflow_store, "scheduled_workflows", return_value=workflows), \
             patch.object(flowengine.workflow_store, "latest_run", return_value=None), \
             patch.object(flowengine.workflow_store, "create_scheduled_run",
                          side_effect=[ValueError("in progress"), 44]), \
             patch.object(flowengine, "start_run") as start:
            self.assertEqual(flowengine.run_due_schedules(), [44])
        sweep.assert_called_once_with(flowengine.RUN_STALE_AFTER_SECONDS)
        start.assert_called_once_with(44)

    def test_document_trigger_skips_a_workflow_with_a_run_in_flight(self) -> None:
        docs = [{"id": 1, "title": "Runbook", "source_id": 4, "source_path": "docs/runbook.md"}]
        workflows = [
            {"id": 1, "name": "A", "trigger": {"on": "document_changed"}},
            {"id": 2, "name": "B", "trigger": {"on": "document_changed"}},
        ]
        with patch.object(flowengine.workflow_store, "trigger_inputs", return_value=(docs, workflows, {})), \
             patch.object(flowengine.workflow_store, "create_triggered_run",
                          side_effect=[ValueError("in progress"), 45]), \
             patch.object(flowengine, "start_run") as start:
            self.assertEqual(flowengine.fire_document_triggers([1], "document_changed"), [45])
        start.assert_called_once_with(45)

    def test_startup_reconciliation_also_sweeps_lost_heartbeats(self) -> None:
        with patch.object(flowengine.workflow_store, "reconcile_stale_runs", return_value=1), \
             patch.object(flowengine.workflow_store, "fail_stale_runs", return_value=2) as sweep:
            self.assertEqual(flowengine.reconcile_stale_runs(), 3)
        sweep.assert_called_once_with(flowengine.RUN_STALE_AFTER_SECONDS)

    def test_stale_sweep_fails_only_running_rows_past_the_heartbeat_threshold(self) -> None:
        conn = _ScriptedConnection([[{"id": 5}], None])
        with patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            self.assertEqual(flowengine.workflow_store.fail_stale_runs(600), 1)
        sql, args = conn.calls[0]
        self.assertIn("SET status = 'failed'", sql)
        self.assertIn("WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %s)", sql)
        self.assertEqual(args, ("no heartbeat for 10 min; marked failed", 600.0))
        self.assertIn("INSERT INTO events", conn.calls[1][0])

    def test_scheduled_run_takes_the_workflow_lock_and_refuses_a_concurrent_run(self) -> None:
        workflow = {"id": 1, "project_id": 3, "name": "A"}
        conn = _ScriptedConnection([{"?column?": 1}, {"?column?": 1}])
        with patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            with self.assertRaisesRegex(ValueError, "already has a run in progress"):
                flowengine.workflow_store.create_scheduled_run(workflow, {"on": "schedule"}, "label")
        self.assertIn("FROM workflows WHERE project_id IS NOT DISTINCT FROM %s AND id = %s FOR UPDATE",
                      conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], (3, 1))
        self.assertIn("status = 'running'", conn.calls[1][0])
        self.assertFalse(any("INSERT INTO workflow_runs" in sql for sql, _ in conn.calls))

    def test_scheduled_run_inserts_once_the_lock_shows_no_run_in_flight(self) -> None:
        workflow = {"id": 1, "project_id": 3, "name": "A"}
        conn = _ScriptedConnection([{"?column?": 1}, None, {"id": 8, "number": 100008}, None])
        with patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            self.assertEqual(flowengine.workflow_store.create_scheduled_run(workflow, {"on": "schedule"}, "label"), 8)
        self.assertIn("INSERT INTO workflow_runs", conn.calls[2][0])

    def test_triggered_run_takes_the_same_lock_and_check(self) -> None:
        context = access.external_access(3, "acme", "Acme", "test", "runner")
        workflow = {"id": 1, "name": "A"}
        conn = _ScriptedConnection([None])
        with access.use_access(context), patch.object(flowengine.workflow_store.db, "connect", return_value=conn):
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                flowengine.workflow_store.create_triggered_run(workflow, [1], {"on": "document_changed"}, "note")
        self.assertIn("FOR UPDATE", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], (3, 1))
        self.assertFalse(any("INSERT INTO workflow_runs" in sql for sql, _ in conn.calls))


if __name__ == "__main__":
    unittest.main()

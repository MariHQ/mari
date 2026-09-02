from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mari_server.knowledge import service
from mari_server.persistence.postgres import fact_intelligence as store


class RecordingResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class RecordingConnection:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        return self.results.pop(0) if self.results else RecordingResult()

    def transaction(self):
        return nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FactRepresentationTests(unittest.TestCase):
    def test_structured_fact_renders_bounded_inspectable_embedding_components(self):
        structured = {
            "atomic_claims": [
                "Developer Experience owns production Kubernetes.",
                "The change begins April 15, 2026.",
            ],
            "subject": {"canonical": "production Kubernetes", "aliases": ["prod k8s"]},
            "relation": "is owned by",
            "object": "Developer Experience",
            "scopes": ["environment:production"],
            "valid_from": "2026-04-15T00:00:00Z",
            "valid_to": None,
            "conditions": ["after the platform handoff"],
        }

        components = service._fact_component_texts(
            "Developer Experience owns production Kubernetes from April 15, 2026.",
            structured,
            limit=20,
        )

        self.assertEqual(components[0]["role"], "claim")
        self.assertIn({"role": "scope", "text": "environment:production"}, components)
        self.assertIn({"role": "object", "text": "Developer Experience"}, components)
        self.assertTrue(any(row["role"] == "time" and "2026-04-15" in row["text"]
                            for row in components))
        self.assertLessEqual(len(service._fact_component_texts("x", structured, limit=3)), 3)

    def test_component_write_replaces_only_one_versioned_profile(self):
        conn = RecordingConnection([RecordingResult(row={"owned": 1}), RecordingResult()])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            store.replace_components(
                41,
                embedding_profile="openai:text-embedding-3-small:v1",
                representation_profile="fact-components-v1",
                provider="openai",
                model="text-embedding-3-small",
                components=[{"role": "claim", "text": "Retention is 30 days.",
                             "embedding": [0.1, 0.2, 0.3]}],
            )

        delete_sql, delete_args = conn.calls[1]
        self.assertIn("DELETE FROM fact_representation_components", delete_sql)
        self.assertEqual(delete_args, (7, 41, "openai:text-embedding-3-small:v1",
                                       "fact-components-v1"))
        insert_sql, insert_args = conn.calls[2]
        self.assertIn("INSERT INTO fact_representation_components", insert_sql)
        self.assertEqual(insert_args[4:7], (0, "claim", "Retention is 30 days."))
        self.assertEqual(insert_args[10], 3)

    def test_postgres_neighbor_query_is_set_to_set_mean_maxsim(self):
        conn = RecordingConnection([RecordingResult(rows=[])])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            store.assertion_neighbors(
                41, "profile-a", "fact-components-v1", limit=8,
                minimum_similarity=.72,
            )

        sql, args = conn.calls[0]
        self.assertIn("max(1 - (target.embedding <=> query.embedding))", sql)
        self.assertIn("avg(similarity)", sql)
        self.assertIn("assertion.status = 'active'", sql)
        self.assertEqual(args[-2:], (.72, 8))

    def test_llm_reservation_is_one_atomic_bounded_update(self):
        conn = RecordingConnection([RecordingResult(row={"id": 9})])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            reserved = store.reserve_llm_call(
                22, stage="adjudicate_facts", purpose="relations",
                estimated_input_tokens=800, output_tokens=300,
            )

        self.assertTrue(reserved)
        sql, args = conn.calls[0]
        self.assertIn("calls_used < max_calls", sql)
        self.assertIn("input_tokens + %s <= max_input_tokens", sql)
        self.assertIn("output_tokens + %s <= max_output_tokens", sql)
        self.assertEqual(args, (800, 300, 7, 22, "adjudicate_facts", "relations", 800, 300))

    def test_adjudication_is_bounded_visible_and_can_abstain(self):
        packet = {
            "assertion": {"id": 41, "claim": "Retention is 10 days.",
                          "structured_claim": {}, "valid_from": None, "valid_to": None},
            "relations": [{"target_assertion_id": 12, "claim": "Retention is 30 days.",
                           "structured_claim": {}, "valid_from": None, "valid_to": None,
                           "recorded_from": None, "exact_score": .94,
                           "criticality": "high"}],
            "evidence": [{"span_id": 90, "document_id": 7, "title": "Policy",
                          "source": "confluence", "quote": "Retention is 10 days.",
                          "source_authority": "approved", "published_at": None,
                          "effective_from": None, "effective_to": None,
                          "revised_at": None, "ingested_at": None, "similarity": .9}],
        }
        result = {
            "recommendation": "needs_review", "relation": "insufficient",
            "target_assertion_id": 12, "valid_from": None, "valid_to": None,
            "confidence": .55, "reason": "Authority conflict", "needs_human_review": True,
            "evidence_groups": [{"span_ids": [90, 999], "verdict": "insufficient",
                                 "sufficient": False, "confidence": .55,
                                 "explanation": "One source is not enough."}],
        }
        configure = Mock()
        complete = Mock()
        save = Mock()
        with patch.object(service.fact_store, "configure_llm_budget", configure), \
             patch.object(service.fact_store, "run_assertion_ids", return_value=[41]), \
             patch.object(service.fact_store, "adjudication_packet", return_value=packet), \
             patch.object(service.fact_store, "reserve_llm_call", return_value=True) as reserve, \
             patch.object(service.fact_store, "save_adjudication", save), \
             patch.object(service.fact_store, "complete_llm_budget", complete), \
             patch.object(service.llm, "generation_model", return_value=("gateway", "model")), \
             patch.object(service.llm, "embedding_profile", return_value="embed-profile"), \
             patch.object(service.llm, "generate_json", return_value=result) as generate:
            stats = service.adjudicate_fact_candidates(
                22, enabled=True, max_calls=1, max_input_tokens=5000,
                max_output_tokens=800, output_tokens_per_call=800,
            )

        self.assertEqual(stats, {"llm_calls": 1, "adjudicated_facts": 1,
                                 "llm_abstentions": 1, "llm_budget_exhausted": 0})
        self.assertEqual(configure.call_args.kwargs["max_calls"], 1)
        reserve.assert_called_once()
        generate.assert_called_once()
        judge_prompt = generate.call_args.kwargs["system"]
        self.assertIn("Reject questions, requests, headings, document commentary", judge_prompt)
        self.assertIn("Do not turn an absence of evidence into a fact", judge_prompt)
        saved = save.call_args.args[1]
        self.assertEqual(saved["evidence_groups"][0]["span_ids"], [90])
        complete.assert_called_once_with(
            22, stage="adjudicate_facts",
            purpose="temporal evidence and relation proposals", status="completed",
        )

    def test_review_gate_reads_candidates_that_never_got_an_assertion(self):
        conn = RecordingConnection([RecordingResult(rows=[])])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            store.adjudication_reviews(91)

        sql, args = conn.calls[0]
        # LEFT JOIN: a candidate whose impact stage was skipped or whose
        # embedding failed still reaches the review pass as a deferral,
        # instead of silently staying pending outside the loop.
        self.assertIn("LEFT JOIN fact_assertions", sql)
        self.assertEqual(args, (7, 91))

    def test_candidate_without_adjudication_defers_to_a_human(self):
        rows = [{"candidate_id": 5, "review_status": "pending",
                 "adjudication": None, "confidence": None}]
        with patch.object(service.fact_store, "adjudication_reviews", return_value=rows), \
             patch.object(service.knowledge_store, "review_fact_candidate") as review, \
             patch.object(service.knowledge_store, "fact_candidate_counts",
                          return_value={"pending": 1, "accepted": 0, "rejected": 0}), \
             patch.object(service.llm, "model_identity", return_value="ollama:model"):
            counts = service.apply_ai_fact_proposals(91)

        self.assertEqual(counts["deferred"], 1)
        review.assert_not_called()

    def test_extraction_budget_refusal_is_visible_and_leaves_documents_unscanned(self):
        docs = [
            {"id": 1, "title": "Alpha", "source": "upload", "body": "Alpha holds.", "snippet": ""},
            {"id": 2, "title": "Beta", "source": "upload", "body": "Beta holds.", "snippet": ""},
        ]
        complete = Mock()
        marked = Mock()
        with patch.object(service, "_scan_batch", return_value=docs), \
             patch.object(service, "_mark_scanned", marked), \
             patch.object(service, "audit"), \
             patch.object(service, "step_progress"), \
             patch.object(service, "component_extract_facts") as extract, \
             patch.object(service.knowledge_store, "fact_claims", return_value=set()), \
             patch.object(service.llm, "generation_model", return_value=("ollama", "model")), \
             patch.object(service.fact_store, "configure_llm_budget"), \
             patch.object(service.fact_store, "reserve_llm_call", return_value=False), \
             patch.object(service.fact_store, "complete_llm_budget", complete):
            candidates, scanned, note = service.extract_fact_candidates_for(
                [1, 2], run_id=91, max_llm_calls=2,
            )

        # A refused reservation is not an empty read: nothing is extracted,
        # the documents stay unscanned for the next rotation, the note says
        # what happened, and the budget row closes exhausted, not completed.
        self.assertEqual(candidates, [])
        self.assertEqual(scanned, 0)
        self.assertIn("2 documents skipped after the LLM token budget ran out", note)
        extract.assert_not_called()
        marked.assert_called_once_with("facts", [])
        complete.assert_called_once_with(
            91, stage="scan_facts", purpose="structured fact extraction",
            status="exhausted",
        )

    def test_extraction_gives_each_call_the_recipe_budget_not_a_slice(self):
        # 20000 output tokens over a 50-call limit used to hand every call
        # 400 tokens; the structured recipe needs ~1000, so every answer was
        # truncated mid-JSON and the whole stage failed. Each call now gets
        # the full per-call allowance and the reservation spends the budget.
        docs = [{"id": 1, "title": "Alpha", "source": "upload", "body": "Alpha holds.", "snippet": ""}]
        configure = Mock()
        with patch.object(service, "_scan_batch", return_value=docs), \
             patch.object(service, "_mark_scanned"), \
             patch.object(service, "audit"), \
             patch.object(service, "step_progress"), \
             patch.object(service, "component_extract_facts", return_value=[]), \
             patch.object(service.knowledge_store, "fact_claims", return_value=set()), \
             patch.object(service.llm, "generation_model", return_value=("ollama", "model")), \
             patch.object(service.fact_store, "configure_llm_budget", configure), \
             patch.object(service.fact_store, "reserve_llm_call", return_value=True), \
             patch.object(service.fact_store, "complete_llm_budget"):
            service.extract_fact_candidates_for(
                [1], run_id=91, max_llm_calls=50, max_output_tokens=20000,
            )
        visible = configure.call_args.kwargs["visible_config"]
        self.assertEqual(visible["output_tokens_per_call"], 2000)

    def test_restore_reopens_the_assertion_and_demands_a_fresh_verification(self):
        conn = RecordingConnection([
            RecordingResult(row={"id": 5, "claim": "Retention is 30 days.",
                                 "current_assertion_id": 41, "invalidated_at": "2026-09-01"}),
            RecordingResult(), RecordingResult(),
        ])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            result = store.restore_fact(5)

        self.assertEqual(result, {"id": 5, "claim": "Retention is 30 days."})
        select_sql = conn.calls[0][0]
        # only an invalidated fact restores; retired stays closed
        self.assertIn("status = 'Invalidated'", select_sql)
        assertion_sql, assertion_args = conn.calls[1]
        self.assertIn("SET status = 'active', recorded_to = NULL", assertion_sql)
        # the boundary the invalidation stamped clears; an earlier one is kept
        self.assertIn("CASE WHEN valid_to = %s THEN NULL ELSE valid_to END", assertion_sql)
        self.assertEqual(assertion_args, ("2026-09-01", 7, 41))
        fact_sql, _ = conn.calls[2]
        # Needs review, never Verified: nobody re-verified anything yet
        self.assertIn("SET status = 'Needs review', invalidated_at = NULL", fact_sql)

    def test_published_facts_belong_to_the_person_who_accepted_them(self):
        from mari_server.persistence.postgres import knowledge as knowledge_store

        candidates = [
            {"id": 1, "claim": "Backups cover both volumes.", "source_label": "Mari scan · Runbook",
             "document_id": 4, "review_kind": "human", "reviewer": "Eric Disque"},
            {"id": 2, "claim": "The API uses Recreate.", "source_label": "Mari scan · Runbook",
             "document_id": 4, "review_kind": "ai", "reviewer": "Bounded AI proposal · ollama:model"},
        ]

        class Conn:
            def __init__(self):
                self.calls = []

            def execute(self, sql, args=()):
                normalized = " ".join(sql.split())
                self.calls.append((normalized, args))
                result = Mock()
                result.fetchall.return_value = (
                    candidates if normalized.startswith("SELECT * FROM fact_extraction_candidates") else [])
                result.fetchone.return_value = (
                    {"id": 7, "current_assertion_id": None}
                    if normalized.startswith("INSERT INTO facts") else None)
                return result

            def transaction(self):
                from contextlib import nullcontext
                return nullcontext()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        conn = Conn()
        context = SimpleNamespace(project_id=7)
        with patch.object(knowledge_store.access, "require_current_access", return_value=context), \
             patch.object(knowledge_store.db, "connect", return_value=conn):
            knowledge_store.publish_fact_candidates(91, "Mari")

        owners = [args[4] for sql, args in conn.calls if sql.startswith("INSERT INTO facts")]
        # The human reviewer owns what they accepted; the AI-accepted claim
        # keeps the automation actor, because no person vouched for it.
        self.assertEqual(owners, ["Eric Disque", "Mari"])

    def test_human_adoption_takes_over_a_machine_owned_fact(self):
        from mari_server.persistence.postgres import knowledge as knowledge_store

        candidates = [
            {"id": 1, "claim": "Backups cover both volumes.", "source_label": "Mari scan · Runbook",
             "document_id": 4, "review_kind": "human", "reviewer": "Eric Disque"},
            {"id": 2, "claim": "The API uses Recreate.", "source_label": "Mari scan · Runbook",
             "document_id": 4, "review_kind": "ai", "reviewer": "Bounded AI proposal · ollama:model"},
        ]

        class Conn:
            def __init__(self):
                self.calls = []

            def execute(self, sql, args=()):
                normalized = " ".join(sql.split())
                self.calls.append((normalized, args))
                result = Mock()
                result.fetchall.return_value = (
                    candidates if normalized.startswith("SELECT * FROM fact_extraction_candidates") else [])
                # every claim already exists: the insert conflicts away and
                # the follow-up select finds the machine-published fact
                result.fetchone.return_value = (
                    {"id": 9, "current_assertion_id": None}
                    if normalized.startswith("SELECT id, current_assertion_id FROM facts") else None)
                return result

            def transaction(self):
                from contextlib import nullcontext
                return nullcontext()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        conn = Conn()
        context = SimpleNamespace(project_id=7)
        with patch.object(knowledge_store.access, "require_current_access", return_value=context), \
             patch.object(knowledge_store.db, "connect", return_value=conn):
            knowledge_store.publish_fact_candidates(91, "Mari")

        transfers = [args for sql, args in conn.calls
                     if sql.startswith("UPDATE facts SET owner_name")]
        # The human adoption takes the fact over from the service identity —
        # and only from the service identity, which the WHERE clause pins.
        # The AI-accepted candidate transfers nothing.
        self.assertEqual(transfers, [("Eric Disque", 7, 9, "Mari")])

    def test_all_timeouts_defer_the_documents_instead_of_failing_the_run(self):
        docs = [
            {"id": 1, "title": "Alpha", "source": "upload", "body": "Alpha holds.", "snippet": ""},
            {"id": 2, "title": "Beta", "source": "upload", "body": "Beta holds.", "snippet": ""},
        ]
        complete = Mock()
        with patch.object(service, "_scan_batch", return_value=docs), \
             patch.object(service, "_mark_scanned") as marked, \
             patch.object(service, "audit"), \
             patch.object(service, "step_progress"), \
             patch.object(service, "component_extract_facts",
                          side_effect=RuntimeError("cannot reach localhost:11434: TimeoutError")), \
             patch.object(service.knowledge_store, "fact_claims", return_value=set()), \
             patch.object(service.llm, "generation_model", return_value=("ollama", "model")), \
             patch.object(service.fact_store, "configure_llm_budget"), \
             patch.object(service.fact_store, "reserve_llm_call", return_value=True), \
             patch.object(service.fact_store, "complete_llm_budget", complete):
            candidates, scanned, note = service.extract_fact_candidates_for(
                [1, 2], run_id=91, max_llm_calls=2,
            )

        # Time running out is deferral, not failure: a laptop busy enough to
        # starve the local model used to fail every scheduled scan outright.
        self.assertEqual((candidates, scanned), ([], 0))
        self.assertIn("not read because the scan hit its", note)
        marked.assert_called_once_with("facts", [])
        complete.assert_called_once_with(
            91, stage="scan_facts", purpose="structured fact extraction",
            status="completed",
        )

    def test_bounded_ai_reviewer_names_the_generation_model(self):
        from mari_server.providers import models as llm_models

        with patch.object(llm_models, "generation_model", return_value=("ollama", "qwen3:14b")):
            self.assertEqual(llm_models.model_identity(), "ollama:qwen3:14b")
        with patch.object(llm_models, "generation_model", return_value=("", "")):
            self.assertEqual(llm_models.model_identity(), "unconfigured model")

    def test_embedding_clusters_need_no_llm_and_keep_related_members_together(self):
        nodes = [
            {"id": 1, "fact_id": 10, "candidate_id": None, "claim": "Prod uses Kubernetes."},
            {"id": 2, "fact_id": None, "candidate_id": 20, "claim": "Prod clusters use k8s."},
        ]
        edges = [{"source": 2, "target": 1, "score": .91}]
        replace = Mock(return_value=[7])
        with patch.object(service.fact_store, "cluster_graph", return_value=(nodes, edges)), \
             patch.object(service.fact_store, "configure_llm_budget") as configure, \
             patch.object(service.fact_store, "replace_clusters", replace), \
             patch.object(service.fact_store, "complete_llm_budget") as complete, \
             patch.object(service.llm, "generation_model", return_value=("gateway", "model")), \
             patch.object(service.llm, "embedding_profile", return_value="embed-profile"), \
             patch.object(service.llm, "generate_json") as generate:
            stats = service.build_fact_clusters(22, label_mode="off")

        self.assertEqual(stats["fact_clusters"], 1)
        generate.assert_not_called()
        self.assertEqual(configure.call_args.kwargs["max_calls"], 0)
        cluster = replace.call_args.args[1][0]
        self.assertEqual(cluster["stable_key"], "fact:10")
        self.assertEqual({member["assertion_id"] for member in cluster["members"]}, {1, 2})
        complete.assert_called_once_with(
            22, stage="cluster_facts", purpose="fact cluster labels", status="skipped",
        )

    def test_impact_preview_separates_dependencies_from_embedding_neighbors(self):
        conn = RecordingConnection([
            RecordingResult(row={"id": 5, "claim": "Platform owns production Kubernetes.",
                                 "current_assertion_id": 41, "criticality": "high"}),
            RecordingResult(rows=[
                {"id": 1, "downstream_type": "decision", "downstream_id": "adr-7",
                 "downstream_label": "Move ingress", "dependency_type": "used_by_decision",
                 "depth": 1},
                {"id": 2, "downstream_type": "workflow", "downstream_id": "deploy",
                 "downstream_label": "Production deploy", "dependency_type": "used_by_workflow",
                 "depth": 2},
            ]),
            RecordingResult(rows=[
                {"assertion_id": 52, "fact_id": 8, "claim": "Platform owns the cluster fleet.",
                 "similarity": .91},
            ]),
        ])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            preview = store.impact_preview(5)

        self.assertEqual(preview["score"], 24)  # 10 + 8 + possible neighbor + high criticality
        self.assertEqual([row["impact_kind"] for row in preview["items"]],
                         ["direct", "transitive", "possible"])
        self.assertEqual(preview["items"][-1]["dependency_type"], "embedding_neighbor")
        self.assertIn("WITH RECURSIVE impacted", conn.calls[1][0])

    def test_invalidation_closes_time_and_materializes_impact_snapshot(self):
        conn = RecordingConnection([
            RecordingResult(row={"id": 5, "claim": "Platform owns production Kubernetes.",
                                 "current_assertion_id": 41}),
            RecordingResult(rows=[]),
            RecordingResult(rows=[]),
            RecordingResult(),
            RecordingResult(),
            RecordingResult(row={"id": 77}),
        ])
        context = SimpleNamespace(project_id=7)
        with patch.object(store.access, "require_current_access", return_value=context), \
             patch.object(store.db, "connect", return_value=conn):
            result = store.invalidate_fact(
                5, reason="Ownership changed", actor="Raphael",
                effective_at="2026-04-15T00:00:00Z",
            )

        self.assertEqual(result["event_id"], 77)
        assertion_sql, assertion_args = conn.calls[3]
        self.assertIn("status = 'invalidated'", assertion_sql)
        self.assertEqual(assertion_args, ("2026-04-15T00:00:00Z", 7, 41))
        self.assertIn("fact_invalidation_events", conn.calls[5][0])


if __name__ == "__main__":
    unittest.main()


class FactLedgerTests(unittest.TestCase):
    def test_add_fact_arbitrates_on_every_unique_index_and_reports_an_existing_claim(self):
        # facts is unique on (project_id, claim) and, since 0031, on the
        # casefolded canonical key. Naming only the claim index as arbiter
        # let a case variant raise through GraphQL instead of deduplicating.
        import hashlib
        from mari_server.identity import access
        from mari_server.persistence.postgres import knowledge as knowledge_store
        conn = RecordingConnection([RecordingResult(None)])
        context = access.AccessContext(1, 7, "acme", "Acme", "admin", access.CAPABILITIES)
        with access.use_access(context), patch.object(knowledge_store.db, "connect", return_value=conn):
            self.assertFalse(knowledge_store.add_fact("Retention Is 30 Days.", "docs", "Eric", 4))
        sql, args = conn.calls[0]
        normalized = " ".join(sql.split())
        self.assertIn("ON CONFLICT DO NOTHING RETURNING id", normalized)
        self.assertNotIn("ON CONFLICT (project_id, claim)", normalized)
        self.assertEqual(args[0], 7)
        self.assertEqual(args[1], "claim:" + hashlib.sha256(b"retention is 30 days.").hexdigest())

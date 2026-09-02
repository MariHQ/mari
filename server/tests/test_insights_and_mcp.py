from __future__ import annotations

import json
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mari_server.knowledge import graphql as mutations_knowledge
from mari_server.knowledge import service as knowledge_service
from mari_server.destinations import graphql as graphql_destinations
from mari_server.persistence.postgres import mcp as mcp_repository
from mari_server.destinations import mcp


class FactInsightTests(unittest.TestCase):
    def test_ai_review_only_applies_bounded_adjudication_and_defers_uncertainty(self) -> None:
        rows = [
            {"candidate_id": 9, "review_status": "pending", "confidence": .95,
             "adjudication": {"recommendation": "reject", "confidence": .93,
                              "reason": "Newer policy supersedes it.",
                              "needs_human_review": False}},
            {"candidate_id": 10, "review_status": "pending", "confidence": .7,
             "adjudication": {"recommendation": "new_fact", "confidence": .7,
                              "needs_human_review": True}},
        ]
        with patch.object(knowledge_service.fact_store, "adjudication_reviews", return_value=rows), \
             patch.object(knowledge_service.llm, "generate_json") as generate, \
             patch.object(knowledge_service.knowledge_store, "review_fact_candidate") as review, \
             patch.object(knowledge_service.knowledge_store, "fact_candidate_counts",
                          return_value={"pending": 1, "accepted": 0, "rejected": 1}):
            result = knowledge_service.apply_ai_fact_proposals(44)
        generate.assert_not_called()
        review.assert_called_once()
        self.assertFalse(review.call_args.kwargs["accepted"])
        self.assertEqual(review.call_args.args[0], 9)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["deferred"], 1)

    def test_fact_impact_uses_versioned_vectors_maxsim_neighbors_and_temporal_links(self) -> None:
        candidate = {"id": 9, "claim": "Workspace retention period is 10 days.", "document_id": 7}
        source = {"id": 7, "title": "Current policy", "updated_src": "2026-08-30", "content_hash": "doc-7"}
        fact_neighbor = {"assertion_id": 3, "fact_id": 3,
                         "claim": "Workspace retention period is 30 days.", "similarity": .94,
                         "recorded_from": "2026-01-01"}
        doc_neighbor = {"document_id": 8, "chunk_id": 88, "title": "Migration guide",
                        "quote": "Retention is changing.", "similarity": .81,
                        "updated_src": "2026-08-20", "content_hash": "doc-8", "acl": {}}
        with patch.object(knowledge_service, "build_fact_representations",
                          return_value={"embedded_assertions": 2, "embedded_components": 7}), \
             patch.object(knowledge_service.knowledge_store, "fact_candidates", return_value=[candidate]), \
             patch.object(knowledge_service.fact_store, "representation_subjects",
                          return_value=[{"assertion_id": 9, "candidate_id": 9}]), \
             patch.object(knowledge_service.fact_store, "assertion_neighbors",
                          return_value=[fact_neighbor]) as facts, \
             patch.object(knowledge_service.fact_store, "evidence_neighbors",
                          return_value=[doc_neighbor]) as docs, \
             patch.object(knowledge_service.fact_store, "replace_embedding_relations") as relations, \
             patch.object(knowledge_service.fact_store, "replace_embedding_evidence") as evidence, \
             patch.object(knowledge_service.knowledge_store, "document", return_value=source), \
             patch.object(knowledge_service.knowledge_store, "replace_candidate_semantic_links") as replace, \
             patch.object(knowledge_service.llm, "embedding_profile", return_value="openai:model:profile"):
            result = knowledge_service.map_fact_candidate_impact(44)
        facts.assert_called_once()
        docs.assert_called_once()
        relations.assert_called_once()
        evidence.assert_called_once()
        links = replace.call_args.args[2]
        self.assertEqual([link["relation"] for link in links], ["source", "contradicts", "related"])
        self.assertTrue(replace.call_args.kwargs["high_impact"])
        self.assertEqual(result, {"impact_links": 3, "high_impact_facts": 1,
                                  "embedded_assertions": 2, "embedded_components": 7})

    def test_fact_scan_keeps_provenance_deduplicates_and_rejects_metadata(self) -> None:
        docs = [{"id": 7, "title": "Limits", "source": "gdrive", "body": "Exports stop at 10 MB.", "snippet": ""}]
        model = [[
            {"claim": "Exports stop at 10 MB."},
            {"claim": "PR #340 · user · closed · updated 2026-01-17T01:57:54Z"},
        ]]
        with patch.object(knowledge_service, "_scan_batch", return_value=docs), \
             patch.object(knowledge_service, "_scan_concurrently", return_value=(list(zip(docs, model)), 0, 0, [])), \
             patch.object(knowledge_service.knowledge_store, "fact_claims", return_value=set()), \
             patch.object(knowledge_service.knowledge_store, "add_fact", return_value=True) as add, \
             patch.object(knowledge_service, "_mark_scanned") as marked, \
             patch.object(knowledge_service, "audit"):
            added, scanned, note = knowledge_service.scan_facts_for([7])
        self.assertEqual((added, scanned, note), (1, 1, ""))
        self.assertEqual(add.call_args.args[0], "Exports stop at 10 MB.")
        self.assertEqual(add.call_args.args[3], 7)
        marked.assert_called_once_with("facts", [7])

    def test_fact_check_turns_ollama_json_into_review_findings(self) -> None:
        doc = {"id": 4, "title": "SLA", "body": "Retention is 10 days.", "snippet": ""}
        with patch.object(knowledge_service.knowledge_store, "document", return_value=doc), \
             patch.object(knowledge_service.knowledge_store, "fact_claims",
                          return_value={"Retention is 30 days."}), \
             patch.object(knowledge_service.llm, "generate_json", return_value={
                 "assessments": [{
                     "claim": "Retention is 30 days.", "verdict": "contradicted",
                     "explanation": "The document says 10 days.", "confidence": .99,
                     "evidence": [{"document_id": "4", "quote": "Retention is 10 days"}],
                 }],
             }) as model, \
             patch.object(knowledge_service.knowledge_store, "add_finding", return_value=True) as add, \
             patch.object(knowledge_service, "audit"):
            count = mutations_knowledge.MutKnowledge().fact_check(4)
        self.assertEqual(count, 1)
        self.assertIn("Retention is 30 days", model.call_args.args[0])
        self.assertEqual(add.call_args.args[0], 4)


class McpLifecycleTests(unittest.TestCase):
    def test_create_mints_one_time_bearer_and_capability_count(self) -> None:
        writes = []
        project = SimpleNamespace(project_id=7, allows=lambda capability: capability == "destination.manage")
        info = SimpleNamespace(context={"user": {"name": "Admin", "role": "admin"}, "access": project})
        with patch.object(graphql_destinations, "_require_admin", return_value={"name": "Admin"}), \
             patch.object(graphql_destinations.config, "get", return_value="https://cloud.example.test"), \
             patch.object(mcp_repository, "q1", return_value=None), \
             patch.object(mcp_repository, "exec_", side_effect=lambda sql, args=(): writes.append((sql, args))), \
             patch.object(mcp_repository, "audit"):
            token = graphql_destinations.DestinationMutations().create_mcp_server(
                info, "Support KB", "workspace", ["search", "facts"],
            )
        self.assertTrue(token.startswith("mari_mcp_"))
        inserted = writes[0][1]
        self.assertEqual(inserted[0], 7)
        self.assertEqual(inserted[1], "Support KB")
        self.assertEqual(inserted[2], "https://cloud.example.test/mcp/support-kb")
        self.assertEqual(inserted[4], 2)
        self.assertEqual(json.loads(inserted[5]), {"capabilities": ["search", "facts"]})
        self.assertNotEqual(inserted[6], token)

    def test_connection_test_exercises_each_enabled_capability(self) -> None:
        counts = {"documents": 8, "facts": 3, "glossary": 2, "approved_answers": 4, "edges": 5}
        def q1(sql, args=()):
            if "FROM mcp_servers" in sql:
                return {"config": {"capabilities": ["search", "facts", "glossary", "answers", "lineage", "chat"]}}
            return {"n": next(v for table, v in counts.items() if f"FROM {table}" in sql)}
        with patch.object(mcp_repository, "q1", side_effect=q1), patch.object(mcp_repository, "exec_"):
            info = SimpleNamespace(context={"access": SimpleNamespace(project_id=7)})
            result = graphql_destinations.DestinationMutations().test_mcp_server(info, 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checks"], {"search": 8, "facts": 3, "glossary": 2, "answers": 4, "lineage": 5, "chat": 1})


class McpProtocolTests(unittest.TestCase):
    SERVER = {"name": "Support KB", "config": {"capabilities": ["search", "facts"]}}

    def test_initialize_and_tools_list_only_advertise_enabled_capabilities(self) -> None:
        init = mcp.dispatch(self.SERVER, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)
        listed = mcp.dispatch(self.SERVER, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({t["name"] for t in listed["result"]["tools"]}, {"search_documents", "list_facts"})

    def test_tool_call_runs_search_and_rejects_disabled_tool(self) -> None:
        rows = [{"id": 4, "title": "Runbook", "source": "github", "snippet": "Deploy safely"}]
        with patch.object(mcp, "hybrid_search", return_value=rows) as search:
            result = mcp.dispatch(self.SERVER, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "search_documents", "arguments": {"query": "deploy", "limit": 99}}})
        search.assert_called_once_with("deploy", 20)
        self.assertIn("Runbook", result["result"]["content"][0]["text"])
        denied = mcp.dispatch(self.SERVER, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "ask_knowledge", "arguments": {"question": "hi"}}})
        self.assertEqual(denied["error"]["code"], -32602)

    def test_http_endpoint_requires_matching_bearer_and_slug(self) -> None:
        class Request:
            async def body(self):
                return b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
        server = {"id": 4, "name": "Support KB", "config": {"capabilities": ["search"]},
                  "project_id": 7, "project_slug": "acme", "project_name": "Acme"}
        with patch.object(mcp.mcp_repository, "authenticate", return_value=server):
            out = asyncio.run(mcp.mcp_endpoint("support-kb", Request(), "Bearer mari_mcp_token"))
            with self.assertRaises(Exception) as denied:
                asyncio.run(mcp.mcp_endpoint("wrong-slug", Request(), "Bearer mari_mcp_token"))
        self.assertEqual(out["result"], {})
        self.assertEqual(getattr(denied.exception, "status_code", None), 401)


if __name__ == "__main__":
    unittest.main()


class McpTokenLookupTests(unittest.TestCase):
    def test_authenticate_matches_only_the_hash_column(self) -> None:
        # Migration 0036 hashed every legacy plaintext bearer, so the lookup
        # must not fall back to m.token: only the hash reaches the query.
        with patch.object(mcp_repository, "q1", return_value=None) as lookup:
            self.assertIsNone(mcp_repository.authenticate("abc123"))
        sql, args = lookup.call_args.args
        normalized = " ".join(sql.split())
        self.assertIn("WHERE m.token_hash = %s", normalized)
        self.assertNotIn("m.token =", normalized)
        self.assertNotIn("m.token <>", normalized)
        self.assertEqual(args, ("abc123",))

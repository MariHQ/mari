from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mari_server.knowledge import service
from mari_server.persistence.postgres import knowledge
from mari_server.persistence.postgres import workflows
from mari_server.scripts import dedupe_facts


class Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def execute(self, sql, args=()):
        self.calls.append((" ".join(sql.split()), args))
        return Result(self.rows)

    def transaction(self):
        return nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SequenceConnection(Connection):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    def execute(self, sql, args=()):
        self.calls.append((" ".join(sql.split()), args))
        return Result(self.results.pop(0) if self.results else [])


class IncrementalFactScanTests(unittest.TestCase):
    def test_normalized_claim_key_ignores_case_spacing_and_punctuation(self):
        self.assertEqual(
            knowledge.normalize_claim("  Retention—is 30 DAYS. "),
            knowledge.normalize_claim("retention is 30 days"),
        )

    def test_passage_queue_requires_an_unseen_current_chunk_hash_and_scope(self):
        conn = Connection([])
        context = SimpleNamespace(project_id=7)
        with patch.object(knowledge.access, "require_current_access", return_value=context), \
             patch.object(knowledge.db, "connect", return_value=conn):
            knowledge.fact_scan_passages([4, 5], "retention", 12)

        sql, args = conn.calls[0]
        self.assertIn("NOT EXISTS ( SELECT 1 FROM fact_chunk_scans", sql)
        self.assertIn("scanned.content_hash = c.content_hash", sql)
        self.assertIn("plainto_tsquery('english', %s)", sql)
        self.assertEqual(args, (7, [4, 5], "retention", "retention", "%retention%", 12))

    def test_successful_passage_checkpoint_rotates_the_document_and_closes_only_when_complete(self):
        conn = Connection([])
        context = SimpleNamespace(project_id=7)
        passage = {"document_id": 4, "chunk_id": 41, "content_hash": "chunk-v2"}
        with patch.object(knowledge.access, "require_current_access", return_value=context), \
             patch.object(knowledge.db, "connect", return_value=conn):
            knowledge.mark_fact_passages_scanned([passage])
        self.assertIn("INSERT INTO fact_chunk_scans", conn.calls[0][0])
        update_sql = conn.calls[1][0]
        self.assertIn("facts_scanned_at = now()", update_sql)
        self.assertIn("THEN d.content_hash ELSE d.facts_scanned_hash END", update_sql)

    def test_staging_and_checkpoint_share_one_transaction_even_with_no_claims(self):
        conn = Connection([])
        context = SimpleNamespace(project_id=7)
        passage = {"document_id": 4, "chunk_id": 41, "content_hash": "chunk-v2"}
        with patch.object(knowledge.access, "require_current_access", return_value=context), \
             patch.object(knowledge.db, "connect", return_value=conn):
            added = knowledge.stage_fact_candidates(91, [], passages=[passage])
        self.assertEqual(added, 0)
        self.assertIn("INSERT INTO fact_chunk_scans", conn.calls[0][0])
        self.assertIn("UPDATE documents", conn.calls[1][0])

    def test_scoped_scan_sends_the_matching_passage_and_checkpoints_it_once(self):
        docs = [{"id": 4, "title": "Runbook", "source": "github", "body": "whole document",
                 "snippet": "", "content_hash": "document-v2"}]
        passage = {"chunk_id": 41, "document_id": 4, "idx": 3,
                   "content": "## Retention\nRetention is 30 days.",
                   "content_hash": "chunk-v2", "title": "Runbook", "source": "github",
                   "updated_src": "2026-09-02"}
        extract = Mock(return_value=[
            {"claim": "Retention is 30 days.", "evidence": [{"quote": "Retention is 30 days."}]},
            {"claim": "RETENTION IS 30 DAYS", "evidence": [{"quote": "Retention is 30 days."}]},
        ])
        with patch.object(service, "_scan_batch", return_value=docs), \
             patch.object(service.knowledge_store, "fact_scan_passages", return_value=[passage]) as queue, \
             patch.object(service.knowledge_store, "fact_claim_keys", return_value=set()), \
             patch.object(service, "component_extract_facts", extract), \
             patch.object(service, "audit"), patch.object(service, "step_progress"):
            candidates, scanned, note, successful_passages = service.extract_fact_candidates_for(
                [4], passage_query="retention", max_llm_calls=1,
            )

        self.assertEqual((scanned, note), (1, ""))
        self.assertEqual(len(candidates), 1)
        queue.assert_called_once_with([4], "retention", 1)
        document = extract.call_args.args[0][0]
        self.assertEqual(document.body, passage["content"])
        self.assertEqual(extract.call_args.kwargs["maximum_characters"], len(passage["content"]))
        self.assertEqual(successful_passages, [passage])

    def test_completed_scope_does_not_call_the_model_again(self):
        docs = [{"id": 4, "title": "Runbook", "source": "github", "body": "body",
                 "snippet": "", "content_hash": "document-v1"}]
        extract = Mock()
        with patch.object(service, "_scan_batch", return_value=docs), \
             patch.object(service.knowledge_store, "fact_scan_passages", return_value=[]), \
             patch.object(service, "component_extract_facts", extract):
            result = service.extract_fact_candidates_for([4], max_llm_calls=1)
        self.assertEqual(result, ([], 0, "No new or changed passages matched this scope", []))
        extract.assert_not_called()

    def test_fact_document_scope_combines_path_and_pending_passage(self):
        conn = Connection([])
        context = SimpleNamespace(project_id=7)
        with patch.object(workflows.access, "require_current_access", return_value=context), \
             patch.object(workflows.db, "connect", return_value=conn):
            workflows.select_documents(
                trigger_ids=[], tag="canonical", query="retention", limit=20,
                rotation="facts", source_ids=[3], path_glob="docs/security/**",
            )
        sql, args = conn.calls[0]
        self.assertIn("d.source_path LIKE %s", sql)
        self.assertIn("fact_chunk_scans", sql)
        self.assertEqual(args[1:3], ("docs/security/%", "docs/security/%"))

    def test_cleanup_merges_without_deleting_provenance_rows(self):
        conn = SequenceConnection([[{"id": 10}, {"id": 11}, {"id": 12}], [], [], []])
        with patch.object(dedupe_facts.db, "connect", return_value=conn):
            self.assertEqual(dedupe_facts.merge_group(7, [10, 11, 12]), 2)
        sql = " ".join(call[0] for call in conn.calls)
        self.assertIn("merged_into_fact_id", sql)
        self.assertIn("fact_assertions SET status = 'superseded'", sql)
        self.assertNotIn("DELETE", sql)


if __name__ == "__main__":
    unittest.main()

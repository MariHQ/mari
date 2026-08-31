from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()

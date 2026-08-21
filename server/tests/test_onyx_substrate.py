from __future__ import annotations

import datetime as dt
import unittest

from mari_components.substrates import Document, SearchRequest, SourceRegistration, TextSection
from mari_server.substrates.onyx import OnyxSubstrate


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/api/health":
            return {"success": True}
        if path == "/api/admin/search":
            return {"documents": [{
                "document_id": "github:MariHQ/mari:README.md",
                "semantic_identifier": "Mari README",
                "blurb": "Mari manages product knowledge.",
                "source_type": "github",
                "link": "https://github.com/MariHQ/mari/blob/main/README.md",
                "updated_at": "2026-08-20T12:00:00Z",
                "score": 5.0,
                "metadata": {"repository": "MariHQ/mari"},
            }]}
        if path == "/api/search":
            return {"results": [{
                "citation_id": 1,
                "title": "Mari README",
                "content": "Mari manages product knowledge.",
                "source_type": "github",
                "link": "https://github.com/MariHQ/mari/blob/main/README.md",
                "updated_at": "2026-08-20T12:00:00Z",
            }]}
        if path == "/api/onyx-api/ingestion":
            return {"document_id": body["document"]["id"], "already_existed": False}
        if path == "/api/manage/admin/connector/status":
            return [{
                "cc_pair_id": 9,
                "name": "Mari GitHub",
                "status": "active",
                "connector": {"id": 4, "source": "github", "connector_specific_config": {"repo": "MariHQ/mari"}},
                "credential": {"id": 7},
            }]
        if path == "/api/manage/admin/connector":
            return {"id": 4}
        if path == "/api/manage/credential":
            return {"id": 7}
        if path == "/api/manage/connector/4/credential/7":
            return {"success": True, "data": 9}
        if path == "/api/manage/admin/cc-pair/9":
            return {"connector": {"id": 4}, "credential": {"id": 7}}
        if path == "/api/manage/admin/connector/run-once":
            return {"success": True, "data": 71}
        return None


class OnyxSubstrateTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.substrate = OnyxSubstrate(
            "https://onyx.example", "not-logged", transport=self.transport,
        )

    def test_default_search_is_non_generative_and_keeps_canonical_id(self):
        hit = self.substrate.search(SearchRequest("what is mari?"))[0]
        self.assertEqual(hit.document_id, "github:MariHQ/mari:README.md")
        self.assertEqual(hit.updated_at, dt.datetime(2026, 8, 20, 12, tzinfo=dt.timezone.utc))
        self.assertEqual(self.transport.calls[0][1], "/api/admin/search")
        self.assertEqual(self.transport.calls[0][2], {"query": "what is mari?", "filters": {}})

    def test_agentic_search_is_explicit_and_disables_query_expansion(self):
        substrate = OnyxSubstrate(
            "https://onyx.example", "not-logged", search_mode="agentic", transport=self.transport,
        )
        hit = substrate.search(SearchRequest("what is mari?"))[0]
        self.assertTrue(hit.document_id.startswith("onyx:"))
        self.assertEqual(self.transport.calls[0][1], "/api/search")
        self.assertIs(self.transport.calls[0][2]["skip_query_expansion"], True)

    def test_document_upsert_and_delete_preserve_external_identity(self):
        result = self.substrate.upsert_document(Document(
            "mari:fact:1", "Approved fact", "ingestion_api",
            (TextSection("Mari is a product-knowledge system.", "https://mari.guru/facts/1"),),
            updated_at=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
            metadata={"status": "approved"},
        ))
        self.assertEqual(result.document_id, "mari:fact:1")
        payload = self.transport.calls[-1][2]["document"]
        self.assertEqual(payload["id"], "mari:fact:1")
        self.assertEqual(payload["sections"][0]["type"], "text")
        self.substrate.delete_document("mari:fact:1/path")
        self.assertEqual(self.transport.calls[-1][1], "/api/onyx-api/ingestion/mari%3Afact%3A1%2Fpath")

    def test_source_lifecycle_composes_onyx_public_apis(self):
        source = self.substrate.create_source(SourceRegistration(
            "Mari GitHub", "github", {"repo": "MariHQ/mari"}, {"github_token": "secret"},
            refresh_seconds=600,
        ))
        self.assertEqual(source.source_id, "9")
        self.assertEqual(self.substrate.list_sources()[0].kind, "github")
        self.assertEqual(self.substrate.run_source("9"), "71")
        self.assertEqual(self.transport.calls[-1][2]["credential_ids"], [7])
        self.substrate.delete_source("9")
        self.assertEqual(self.transport.calls[-1][:2], ("DELETE", "/api/manage/connector/4/credential/7"))


if __name__ == "__main__":
    unittest.main()

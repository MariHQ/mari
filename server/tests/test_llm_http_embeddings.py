from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.providers import models as llm


class HttpEmbeddingTests(unittest.TestCase):
    def tearDown(self) -> None:
        llm._catalog_cache.update({"at": 0.0, "value": None})

    def test_openai_batch_preserves_order_and_requests_index_width(self) -> None:
        calls = []

        def post(url, payload, headers=None, **kwargs):
            calls.append((url, payload, headers, kwargs))
            return {"data": [
                {"index": 1, "embedding": [0.2] * llm.EMBED_DIMS},
                {"index": 0, "embedding": [0.1] * llm.EMBED_DIMS},
            ]}

        with patch.object(llm, "embedding_model", return_value=("openai", "text-embedding-3-small")), \
             patch.object(llm, "_api_key", return_value="secret"), \
             patch.object(llm, "_post", side_effect=post):
            vectors = llm.embed_many(["first", "second"])

        self.assertEqual([vector[0] for vector in vectors if vector], [0.1, 0.2])
        self.assertEqual(calls[0][0], "https://api.openai.com/v1/embeddings")
        self.assertEqual(calls[0][1], {
            "model": "text-embedding-3-small", "input": ["first", "second"],
            "dimensions": llm.EMBED_DIMS,
        })
        self.assertEqual(calls[0][2], {"Authorization": "Bearer secret"})

    def test_openai_requires_a_key_and_wrong_width_is_rejected(self) -> None:
        with patch.object(llm, "_api_key", return_value=""):
            self.assertEqual(llm._http_embeddings(["x"], "openai", "text-embedding-3-small"), [None])
            self.assertIn("no credential", llm.last_error())
        with patch.object(llm, "_api_key", return_value="secret"), \
             patch.object(llm, "_post", return_value={"data": [{"index": 0, "embedding": [1.0] * 384}]}):
            self.assertEqual(llm._http_embeddings(["x"], "openai", "text-embedding-3-small"), [None])
            self.assertIn("768", llm.last_error())

    def test_deployment_selects_generation_and_embedding_independently(self) -> None:
        values = {
            ("models", "generation_provider"): "gateway",
            ("models", "generation_model"): "deepseek-v4-flash",
            ("models", "embedding_provider"): "openai",
            ("models", "embedding_model"): "text-embedding-3-small",
        }
        with patch.object(llm.config, "get", side_effect=lambda section, key, *default: values.get(
                (section, key), default[0] if default else None)):
            self.assertEqual(llm.generation_model(), ("gateway", "deepseek-v4-flash"))
            self.assertEqual(llm.embedding_model(), ("openai", "text-embedding-3-small"))

    def test_missing_or_partial_selection_does_not_fall_back(self) -> None:
        with patch.object(llm, "embedding_model", return_value=("", "")):
            self.assertIsNone(llm.embed("x"))
            self.assertEqual(llm.last_error(), "embedding provider and model must both be configured")
        with patch.object(llm, "generation_model", return_value=("gateway", "")):
            self.assertIsNone(llm.generate("x"))
            self.assertEqual(llm.last_error(), "generation provider and model must both be configured")

    def test_catalog_contains_only_http_embedding_providers(self) -> None:
        def post(url, payload=None, *args, **kwargs):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "embedder"}, {"name": "chat"}]}
            if url.endswith("/api/show"):
                return {"capabilities": ["embedding"] if payload["model"] == "embedder" else ["completion"]}
            if url.endswith("/models"):
                return {"data": [{"id": "deepseek-v4-flash"}]}
            self.fail(url)

        gateway = {"base_url": "https://api.deepseek.com", "token": "secret",
                   "headers": {}, "metadata": {}, "model_header": "", "max_retries": 0,
                   "compatibility": "deepseek"}
        with patch.object(llm, "embedding_model", return_value=("openai", "text-embedding-3-small")), \
             patch.object(llm, "generation_model", return_value=("gateway", "deepseek-v4-flash")), \
             patch.object(llm, "gateway_config", return_value=gateway), \
             patch.object(llm, "_post", side_effect=post):
            catalog = llm.model_catalog(refresh=True)

        self.assertEqual(catalog["embedding"], ["ollama:embedder", "openai:text-embedding-3-small"])
        self.assertNotIn("sentence", str(catalog).lower())


if __name__ == "__main__":
    unittest.main()

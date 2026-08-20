from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from mari_server.providers import models as llm


class _Model:
    def __init__(self, vector):
        self.vector = vector
        self.calls = []

    def encode(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.vector


class SentenceTransformerEmbeddingTests(unittest.TestCase):
    def tearDown(self) -> None:
        llm._sentence_models.clear()
        llm._catalog_cache.update({"at": 0.0, "value": None})

    def test_local_sentence_transformer_returns_normalized_native_768_vector(self):
        model = _Model(np.full(llm.EMBED_DIMS, 0.125, dtype=np.float32))
        with patch.object(llm, "embedding_model", return_value=(
                "sentence-transformers", "sentence-transformers/all-mpnet-base-v2")), \
             patch.object(llm, "_sentence_model", return_value=model):
            vector = llm.embed("Product knowledge")

        self.assertEqual(len(vector or []), llm.EMBED_DIMS)
        self.assertEqual(model.calls, [("Product knowledge", {
            "normalize_embeddings": True, "convert_to_numpy": True,
        })])

    def test_wrong_width_and_load_failure_degrade_with_actionable_errors(self):
        with patch.object(llm, "embedding_model", return_value=("sentence-transformers", "small")), \
             patch.object(llm, "_sentence_model", return_value=_Model(np.ones(384))):
            self.assertIsNone(llm.embed("x"))
            self.assertIn("768", llm.last_error())

        with patch.object(llm, "embedding_model", return_value=("sentence-transformers", "missing")), \
             patch.object(llm, "_sentence_model", side_effect=OSError("secret cache path")):
            self.assertIsNone(llm.embed("x"))
            self.assertEqual(llm.last_error(), "sentence-transformers model 'missing' failed (OSError)")

    def test_deployment_environment_can_select_generation_and_embedding_independently(self):
        values = {
            ("models", "generation_provider"): "gateway",
            ("models", "generation_model"): "deepseek-v4-flash",
            ("models", "embedding_provider"): "sentence-transformers",
            ("models", "embedding_model"): "sentence-transformers/all-mpnet-base-v2",
        }
        with patch.object(llm.config, "get", side_effect=lambda section, key, *default: values.get(
                (section, key), default[0] if default else None)):
            self.assertEqual(llm.generation_model(), ("gateway", "deepseek-v4-flash"))
            self.assertEqual(llm.embedding_model(), (
                "sentence-transformers", "sentence-transformers/all-mpnet-base-v2"))

    def test_missing_or_partial_selection_does_not_fall_back(self):
        with patch.object(llm, "embedding_model", return_value=("", "")):
            self.assertIsNone(llm.embed("x"))
            self.assertEqual(llm.last_error(),
                             "embedding provider and model must both be configured")
        with patch.object(llm, "generation_model", return_value=("gateway", "")):
            self.assertIsNone(llm.generate("x"))
            self.assertEqual(llm.last_error(),
                             "generation provider and model must both be configured")

    def test_legacy_default_metadata_is_not_an_executable_fallback(self):
        self.assertEqual(llm._resolve({
            "default": "ollama:nomic-embed-text",
            "options": ["ollama:nomic-embed-text"],
        }), ("", ""))

    def test_catalog_uses_provider_capabilities_and_gateway_models(self):
        def post(url, payload=None, *args, **kwargs):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "embedder"}, {"name": "chat"}, {"name": "opaque"}]}
            if url.endswith("/api/show"):
                return {
                    "embedder": {"capabilities": ["embedding"]},
                    "chat": {"capabilities": ["completion", "tools"]},
                    "opaque": {"capabilities": []},
                }[payload["model"]]
            if url.endswith("/models"):
                return {"data": [{"id": "deepseek-v4-flash"}]}
            self.fail(url)

        gateway = {"base_url": "https://api.deepseek.com", "token": "secret",
                   "headers": {}, "metadata": {}, "model_header": "", "max_retries": 0,
                   "compatibility": "deepseek"}
        with patch.object(llm, "embedding_model", return_value=(
                "sentence-transformers", "sentence-transformers/all-mpnet-base-v2")), \
             patch.object(llm, "generation_model", return_value=(
                 "gateway", "deepseek-v4-flash")), \
             patch.object(llm, "gateway_config", return_value=gateway), \
             patch.object(llm, "_post", side_effect=post):
            catalog = llm.model_catalog(refresh=True)

        self.assertEqual(catalog["embedding"], [
            "ollama:embedder",
            "sentence-transformers:sentence-transformers/all-mpnet-base-v2",
        ])
        self.assertEqual(catalog["generation"], [
            "gateway:deepseek-v4-flash",
            "ollama:chat",
        ])
        self.assertNotIn("opaque", str(catalog))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from mari_server.product import queries


class EffectiveModelSettingsTests(unittest.TestCase):
    def test_deployment_generation_and_embedding_are_reported_as_effective(self) -> None:
        with patch.object(queries.llm, "generation_model", return_value=("gateway", "deepseek-v4-flash")), \
             patch.object(queries.llm, "gateway_config", return_value={
                 "base_url": "https://api.deepseek.com", "token": "secret",
                 "compatibility": "deepseek", "headers": {}, "metadata": {},
                 "model_header": "", "max_retries": 2,
             }), \
             patch.object(queries.llm, "embedding_model", return_value=(
                 "sentence-transformers", "sentence-transformers/all-mpnet-base-v2",
             )):
            generation = queries._effective_model_setting(
                "llm", {"provider": "ollama", "model": "gemma3:4b"})
            embedding = queries._effective_model_setting(
                "embedding", {"provider": "ollama", "model": "nomic-embed-text", "dims": 768})

        self.assertEqual((generation["provider"], generation["model"]),
                         ("gateway", "deepseek-v4-flash"))
        self.assertEqual(generation["gateway"]["compatibility"], "deepseek")
        self.assertEqual((embedding["provider"], embedding["model"]),
                         ("sentence-transformers", "sentence-transformers/all-mpnet-base-v2"))
        self.assertEqual(embedding["dims"], 768)

    def test_effective_gateway_secret_is_masked_before_graphql_response(self) -> None:
        with patch.object(queries.llm, "generation_model", return_value=("gateway", "deepseek-v4-flash")), \
             patch.object(queries.llm, "gateway_config", return_value={
                 "base_url": "https://api.deepseek.com", "token": "runtime-secret",
                 "compatibility": "deepseek", "headers": {}, "metadata": {},
                 "model_header": "", "max_retries": 2,
             }):
            effective = queries._effective_model_setting("llm", {
                "provider": "ollama", "model": "gemma3:4b",
            })
        masked = queries._mask_setting("llm", effective)
        self.assertNotIn("runtime-secret", str(masked))
        self.assertIn("•", masked["gateway"]["token"])


if __name__ == "__main__":
    unittest.main()

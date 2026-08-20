from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import llm


class OllamaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        llm.reload_settings()

    def test_embedding_uses_ollama_contract_and_enforces_index_width(self) -> None:
        vector = [0.25] * llm.EMBED_DIMS
        with patch.object(llm, "_settings", return_value=({}, {
                 "provider": "ollama", "model": "nomic-embed-text"})), \
             patch.object(llm.config, "get", side_effect=lambda section, key: {
                 ("ollama", "host"): "http://ollama:11434/",
                 ("ollama", "embed_model"): "nomic-embed-text",
             }.get((section, key))), \
             patch.object(llm, "_post", return_value={"embedding": vector}) as post:
            self.assertEqual(llm.embed("policy text"), vector)
        url, payload = post.call_args.args[:2]
        self.assertEqual(url, "http://ollama:11434/api/embeddings")
        self.assertEqual(payload, {"model": "nomic-embed-text", "prompt": "policy text"})

        with patch.object(llm, "embedding_model", return_value=("ollama", "wrong-width")), \
             patch.object(llm, "_post", return_value={"embedding": [1.0, 2.0]}):
            self.assertIsNone(llm.embed("x"))
            self.assertIn("768", llm.last_error())

    def test_generation_and_json_use_non_streaming_ollama(self) -> None:
        with patch.object(llm, "generation_model", return_value=("ollama", "gemma3:4b")), \
             patch.object(llm, "ollama_host", return_value="http://ollama:11434"), \
             patch.object(llm, "_post", return_value={"response": '```json\n[{"claim":"The limit is 10."}]\n```'}) as post:
            out = llm.generate_json("extract facts", "fact extractor")
        self.assertEqual(out, [{"claim": "The limit is 10."}])
        url, payload = post.call_args.args[:2]
        self.assertEqual(url, "http://ollama:11434/api/generate")
        self.assertEqual(payload["model"], "gemma3:4b")
        self.assertFalse(payload["stream"])
        self.assertIn("ONLY valid JSON", payload["prompt"])

    def test_unreachable_ollama_degrades_with_an_actionable_error(self) -> None:
        with patch.object(llm, "generation_model", return_value=("ollama", "gemma3:4b")), \
             patch.object(llm, "_post", side_effect=lambda *a, **k: (llm._fail("cannot reach ollama:11434") or None)):
            self.assertIsNone(llm.generate("hello"))
        self.assertEqual(llm.last_error(), "cannot reach ollama:11434")


@unittest.skipUnless(os.environ.get("MARI_TEST_LIVE_OLLAMA") == "1", "set MARI_TEST_LIVE_OLLAMA=1")
class LiveOllamaTests(unittest.TestCase):
    def test_installed_models_generate_and_embed(self) -> None:
        def cfg(section: str, key: str):
            return {
                ("ollama", "host"): os.environ.get("MARI_OLLAMA_HOST", "http://localhost:11434"),
                ("ollama", "embed_model"): os.environ.get("MARI_TEST_EMBED_MODEL", "nomic-embed-text"),
                ("ollama", "gen_model"): os.environ.get("MARI_TEST_GEN_MODEL", "gemma3:4b"),
            }.get((section, key))

        with patch.object(llm, "_settings", return_value=(
                {"provider": "ollama", "model": os.environ.get("MARI_TEST_GEN_MODEL", "gemma3:4b")},
                {"provider": "ollama", "model": os.environ.get("MARI_TEST_EMBED_MODEL", "nomic-embed-text")}
             )), patch.object(llm.config, "get", side_effect=cfg):
            vector = llm.embed("Mari indexes team documentation.")
            answer = llm.generate("Reply with exactly: ollama-ok", timeout=60)
        self.assertIsNotNone(vector, llm.last_error())
        self.assertEqual(len(vector or []), llm.EMBED_DIMS)
        self.assertTrue(answer, llm.last_error())


if __name__ == "__main__":
    unittest.main()

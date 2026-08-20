from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

from mari_server.providers import models as llm
from mari_server.operations import telemetry as observability


class Response:
    def __init__(self, body: dict | None = None, lines: list[bytes] | None = None):
        self.body = json.dumps(body or {}).encode()
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body

    def __iter__(self):
        return iter(self.lines)


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://gateway.test/v1/chat/completions", code, body or "error",
        {"Retry-After": "0"}, io.BytesIO(body.encode()),
    )


class GatewayConfigTests(unittest.TestCase):
    def test_gateway_secrets_are_masked_and_masked_round_trips_preserve_values(self) -> None:
        stored = {"gateway": {"base_url": "https://gateway.test/v1", "token": "real-gateway-token",
                              "headers": {"X-API-Key": "real-header-secret", "X-Tenant": "acme"}},
                  "keys": {"openai": "real-openai-key"}}
        def masker(value):
            return "••••…" + str(value)[-4:]

        masked = {**stored, "gateway": llm.mask_gateway_secrets(stored["gateway"], masker),
                  "keys": {name: masker(value) for name, value in stored["keys"].items()}}
        self.assertNotIn("real-gateway-token", json.dumps(masked))
        self.assertNotIn("real-header-secret", json.dumps(masked))
        self.assertEqual(masked["gateway"]["headers"]["X-Tenant"], "acme")
        merged = llm.preserve_masked(stored, masked)
        self.assertEqual(merged["gateway"]["token"], "real-gateway-token")
        self.assertEqual(merged["gateway"]["headers"]["X-API-Key"], "real-header-secret")
        self.assertEqual(merged["keys"]["openai"], "real-openai-key")

    def test_workspace_config_overrides_env_and_bounds_retries(self) -> None:
        deployment = {
            ("llm_gateway", "base_url"): "https://env.test/v1/",
            ("llm_gateway", "token"): "env-token",
            ("llm_gateway", "headers"): {"X-Env": "yes"},
            ("llm_gateway", "metadata"): {"application": "mari"},
            ("llm_gateway", "model_header"): "X-Model",
            ("llm_gateway", "max_retries"): 2,
            ("llm_gateway", "compatibility"): "openai",
        }
        stored = {"gateway": {"base_url": "https://workspace.test/v1/", "max_retries": 99}}
        with patch.object(llm, "_settings", return_value=(stored, {})), \
             patch.object(llm.config, "get", side_effect=lambda section, key, default=None: deployment.get((section, key), default)):
            cfg = llm.gateway_config()
        self.assertEqual(cfg["base_url"], "https://workspace.test/v1")
        self.assertEqual(cfg["token"], "env-token")
        self.assertEqual(cfg["max_retries"], 5)

    def test_gateway_headers_propagate_trace_and_model_metadata(self) -> None:
        cfg = {"headers": {"X-Tenant": "acme", "X-Routed-Model": "{model}"},
               "token": "top-secret", "model_header": "X-Model-ID"}
        request_token = observability._request_id.set("req-7")
        correlation_token = observability._correlation_id.set("corr-9")
        try:
            headers = llm._gateway_headers(cfg, "llama-3")
        finally:
            observability._request_id.reset(request_token)
            observability._correlation_id.reset(correlation_token)
        self.assertEqual(headers["Authorization"], "Bearer top-secret")
        self.assertEqual(headers["X-Routed-Model"], "llama-3")
        self.assertEqual(headers["X-Model-ID"], "llama-3")
        self.assertEqual(headers["X-Request-ID"], "req-7")
        self.assertEqual(headers["X-Correlation-ID"], "corr-9")


class GatewayTransportTests(unittest.TestCase):
    def test_retries_429_and_5xx_then_succeeds_with_bounded_backoff(self) -> None:
        side_effects = [http_error(429, "secret prompt"), http_error(503), Response({"ok": True})]
        with patch.object(llm.urllib.request, "urlopen", side_effect=side_effects) as open_, \
             patch.object(llm.time, "sleep") as sleep:
            out = llm._post("https://gateway.test/v1/chat/completions", {"prompt": "private"},
                            {"Authorization": "Bearer secret"}, provider_name="gateway", max_retries=2)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(open_.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args[0], 0.0)

    def test_retries_network_timeout_then_succeeds(self) -> None:
        with patch.object(llm.urllib.request, "urlopen",
                          side_effect=[TimeoutError("slow"), Response({"ok": True})]) as open_, \
             patch.object(llm.time, "sleep"):
            out = llm._post("https://gateway.test/v1/models", None,
                            provider_name="gateway", max_retries=1, method="GET")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(open_.call_count, 2)

    def test_auth_and_other_4xx_are_not_retried_or_reflected(self) -> None:
        reflected = 'token=sk-super-secret prompt="board acquisition"'
        for code in (400, 401, 403, 404):
            with self.subTest(code=code), \
                 patch.object(llm.urllib.request, "urlopen", side_effect=http_error(code, reflected)) as open_, \
                 patch.object(llm.time, "sleep") as sleep:
                self.assertIsNone(llm._post("https://gateway.test/v1/chat/completions",
                                           {"prompt": "board acquisition"},
                                           {"Authorization": "Bearer sk-super-secret"},
                                           provider_name="gateway", max_retries=3))
                self.assertEqual(open_.call_count, 1)
                sleep.assert_not_called()
                self.assertNotIn("sk-super-secret", llm.last_error())
                self.assertNotIn("board acquisition", llm.last_error())

    def test_stream_retries_only_before_first_emitted_byte(self) -> None:
        first = urllib.error.URLError(TimeoutError("slow"))
        good = Response(lines=[b'data: {"choices":[]}\n', b"data: [DONE]\n"])
        with patch.object(llm.urllib.request, "urlopen", side_effect=[first, good]) as open_, \
             patch.object(llm.time, "sleep"):
            lines = list(llm._stream("https://gateway.test/v1/chat/completions", {"stream": True},
                                     provider_name="gateway", max_retries=1))
        self.assertEqual(len(lines), 2)
        self.assertEqual(open_.call_count, 2)

    def test_stream_does_not_replay_after_a_byte_was_emitted(self) -> None:
        class Partial(Response):
            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n'
                raise urllib.error.URLError(TimeoutError("lost"))

        with patch.object(llm.urllib.request, "urlopen", return_value=Partial()) as open_, \
             patch.object(llm.time, "sleep") as sleep:
            lines = list(llm._stream("https://gateway.test/v1/chat/completions", {"stream": True},
                                     provider_name="gateway", max_retries=3))
        self.assertEqual(len(lines), 1)
        self.assertEqual(open_.call_count, 1)
        sleep.assert_not_called()


class GatewayContractTests(unittest.TestCase):
    CFG = {"base_url": "https://gateway.test/v1", "token": "gateway-token",
           "headers": {"X-Tenant": "acme"}, "metadata": {"application": "mari"},
           "model_header": "X-Model", "max_retries": 2, "compatibility": "openai"}

    def test_generation_uses_openai_contract_metadata_and_usage_hook(self) -> None:
        response = {"choices": [{"message": {"content": " gateway ok "}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}, "cost": 0.004}
        with patch.object(llm, "generation_model", return_value=("gateway", "corp-chat")), \
             patch.object(llm, "gateway_config", return_value=dict(self.CFG)), \
             patch.object(llm, "_post", return_value=response) as post, \
             patch.object(observability, "record_llm_usage") as usage:
            self.assertEqual(llm.generate("private prompt", "system"), "gateway ok")
        url, payload, headers = post.call_args.args[:3]
        self.assertEqual(url, "https://gateway.test/v1/chat/completions")
        self.assertEqual(payload["model"], "corp-chat")
        self.assertEqual(payload["metadata"], {"application": "mari"})
        self.assertEqual(headers["Authorization"], "Bearer gateway-token")
        self.assertEqual(headers["X-Tenant"], "acme")
        self.assertEqual(headers["X-Model"], "corp-chat")
        self.assertEqual(post.call_args.kwargs["max_retries"], 2)
        usage.assert_called_once_with("gateway", "corp-chat", response["usage"], 0.004)

    def test_embedding_uses_gateway_and_enforces_derived_index_width(self) -> None:
        response = {"data": [{"embedding": [0.5] * llm.EMBED_DIMS}], "usage": {"total_tokens": 4}}
        with patch.object(llm, "embedding_model", return_value=("gateway", "corp-embed")), \
             patch.object(llm, "gateway_config", return_value=dict(self.CFG)), \
             patch.object(llm, "_post", return_value=response) as post:
            vector = llm.embed("knowledge")
        self.assertEqual(len(vector or []), llm.EMBED_DIMS)
        self.assertEqual(post.call_args.args[0], "https://gateway.test/v1/embeddings")
        self.assertEqual(post.call_args.args[1]["metadata"], {"application": "mari"})

    def test_health_is_prompt_free_get_and_has_explicit_misconfiguration(self) -> None:
        with patch.object(llm, "gateway_config", return_value={**self.CFG, "base_url": ""}):
            self.assertEqual(llm.gateway_health()["detail"], "LLM gateway base URL is not configured")
        with patch.object(llm, "gateway_config", return_value={**self.CFG, "base_url": "gateway.test/v1"}):
            self.assertIn("must be an http(s) URL", llm.gateway_health()["detail"])
        with patch.object(llm, "gateway_config", return_value=dict(self.CFG)), \
             patch.object(llm, "_post", return_value={"data": [{"id": "a"}, {"id": "b"}]}) as post:
            health = llm.gateway_health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["models"], 2)
        self.assertIsNone(post.call_args.args[1])
        self.assertEqual(post.call_args.kwargs["method"], "GET")

    def test_deepseek_v4_uses_generation_contract_without_metadata(self) -> None:
        cfg = {**self.CFG, "base_url": "https://api.deepseek.com", "compatibility": "deepseek"}
        response = {"choices": [{"message": {"content": "ready"}}], "usage": {"total_tokens": 3}}
        with patch.object(llm, "generation_model", return_value=("gateway", "deepseek-v4-flash")), \
             patch.object(llm, "gateway_config", return_value=cfg), \
             patch.object(llm, "_post", return_value=response) as post:
            self.assertEqual(llm.generate("health prompt"), "ready")
        payload = post.call_args.args[1]
        self.assertEqual(payload["max_tokens"], 700)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("max_completion_tokens", payload)
        self.assertNotIn("metadata", payload)

    def test_deepseek_json_generation_enables_provider_structured_output(self) -> None:
        cfg = {**self.CFG, "base_url": "https://api.deepseek.com", "compatibility": "deepseek"}
        response = {"choices": [{"message": {"content": '{"action":"answer"}'}}]}
        schema = {"type": "object", "properties": {"action": {"type": "string"}}}
        with patch.object(llm, "generation_model", return_value=("gateway", "deepseek-v4-flash")), \
             patch.object(llm, "gateway_config", return_value=cfg), \
             patch.object(llm, "_post", return_value=response) as post:
            self.assertEqual(llm.generate_json("choose an action", schema=schema), {"action": "answer"})
        payload = post.call_args.args[1]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("json_schema", payload["response_format"])


@unittest.skipUnless(os.environ.get("MARI_TEST_LIVE_DEEPSEEK") == "1" and
                     os.environ.get("MARI_DEEPSEEK_API_KEY"),
                     "set MARI_TEST_LIVE_DEEPSEEK=1 and MARI_DEEPSEEK_API_KEY")
class LiveDeepSeekGatewayTests(unittest.TestCase):
    def test_v4_flash_nonstreaming_and_streaming_contract(self) -> None:
        cfg = {"base_url": "https://api.deepseek.com", "token": os.environ["MARI_DEEPSEEK_API_KEY"],
               "headers": {}, "metadata": {"unsupported": "must-not-be-sent"}, "model_header": "",
               "max_retries": 2, "compatibility": "deepseek"}
        with patch.object(llm, "generation_model", return_value=("gateway", "deepseek-v4-flash")), \
             patch.object(llm, "gateway_config", return_value=cfg):
            answer = llm.generate("Reply with exactly LIVE_OK.", timeout=45)
            streamed = "".join(llm.chat_stream([{"role": "user", "content": "Reply with exactly STREAM_OK."}], ""))
        self.assertIn("LIVE_OK", answer or "")
        self.assertIn("STREAM_OK", streamed)


if __name__ == "__main__":
    unittest.main()

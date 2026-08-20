"""Mari — the model client: embeddings, generation, chat streaming.

DESIGN.md §5/§10: the embedding model and the LLM are configurable. This module
now actually reads that configuration.

It used to hardcode `http://localhost:11434`, `nomic-embed-text` and `gemma3:4b`
while its own docstring claimed otherwise, which made Settings → Models a page
that wrote a row nobody read (DEAD-1) and made `MARI_OLLAMA_HOST` — set by the
compose file precisely because `localhost` inside a container is the container —
a variable with no reader, so the compose deployment could never reach ollama at
all (DEAD-2). Both settings rows are read here now, on every call, through a
short-lived cache.

Each capability has one selection. A deployment may own it with the atomic
provider/model pair in `mari.toml` or the environment; otherwise the admin-owned
`settings.llm` / `settings.embedding` row is authoritative. A partial or
missing selection is an error. Providers are never substituted after an error.

Providers: `ollama` and `local` (a local daemon), `openai`, and `anthropic`
(generation only — Anthropic serves no embedding endpoint). Provider API keys
come from `settings.llm.keys`, which the console already collects and masks.

Every call still degrades rather than raising: `embed` and `generate` return
None when the model is unreachable, misconfigured, or refuses, and callers
already treat None as "no model available". `last_error()` carries the real
reason so a caller that wants to report it can, instead of inventing one.

Import-cycle-free: db.py imports flowengine, which imports this module, so the
settings read is a late import inside the function and a database that is not
up yet simply means "no settings row", not an exception at import time.

Raw HTTP via urllib on purpose. This module is provider-pluggable and stdlib-
only — the server has no vendor SDK dependency for any of the three providers,
and adding one for a single JSON POST would make the deployment heavier without
making the call more correct.
"""

from __future__ import annotations

import json
import email.utils
import socket
import threading
import time
import typing as t
import urllib.error
import urllib.parse
import urllib.request

import config
import observability

# Connection defaults are not provider/model fallbacks. Fresh databases seed
# their explicit model selections in init.sql.
DEFAULT_OLLAMA = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_GEN_MODEL = "gemma3:4b"

# The width of `documents.embedding` and `chunks.embedding` (init.sql:
# vector(768)). A vector of any other length cannot be stored, so a provider
# that cannot produce this width is refused with a reason rather than left to
# fail deep inside an INSERT. OpenAI's text-embedding-3-* models take a
# `dimensions` argument, which is why they can serve this schema at all.
EMBED_DIMS = 768

OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

_SETTINGS_TTL = 30.0
_settings_cache: dict[str, t.Any] = {"at": 0.0, "llm": {}, "embedding": {}}
_settings_lock = threading.Lock()
_last_error = threading.local()
_sentence_models: dict[str, t.Any] = {}
_sentence_models_lock = threading.Lock()
_catalog_cache: dict[str, t.Any] = {"at": 0.0, "value": None}
_catalog_lock = threading.Lock()


def last_error() -> str:
    """Why the most recent call on this thread returned None, or '' if the last
    call succeeded. A real reason, never a guess — callers that surface it are
    quoting the provider, not paraphrasing it."""
    return getattr(_last_error, "message", "")


def _fail(message: str) -> None:
    _last_error.message = message


def _ok() -> None:
    _last_error.message = ""


# ————————————————— configuration —————————————————


def _settings() -> tuple[dict, dict]:
    """(settings.llm, settings.embedding) as dicts, cached briefly.

    A missing table, an unreachable database, or a row that is not an object
    all yield {} — the caller then falls back to config and the defaults, which
    is what a workspace that has never opened Settings → Models should get."""
    now = time.monotonic()
    with _settings_lock:
        if now - _settings_cache["at"] < _SETTINGS_TTL:
            return _settings_cache["llm"], _settings_cache["embedding"]
    llm_cfg: dict = {}
    embed_cfg: dict = {}
    try:
        from db import jload, q  # late: db imports flowengine imports this module
        for row in q("SELECT key, value FROM settings WHERE key IN ('llm', 'embedding')"):
            value = jload(row["value"])
            if isinstance(value, dict):
                (llm_cfg if row["key"] == "llm" else embed_cfg).update(value)
    except Exception:  # noqa: BLE001 — configuration is best-effort, never fatal
        pass
    with _settings_lock:
        _settings_cache.update({"at": now, "llm": llm_cfg, "embedding": embed_cfg})
    return llm_cfg, embed_cfg


def reload_settings() -> None:
    """Drop the settings cache so the next call re-reads the table. Saving on
    Settings → Models should take effect now, not in thirty seconds."""
    with _settings_lock:
        _settings_cache["at"] = 0.0


def preserve_masked(existing: t.Any, incoming: t.Any) -> t.Any:
    """Masked GraphQL reads are placeholders, never replacement secrets."""
    if isinstance(incoming, str) and "•" in incoming:
        return existing
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return {k: preserve_masked(existing.get(k), value) for k, value in incoming.items()}
    return incoming


def mask_gateway_secrets(gateway: dict, masker: t.Callable[[t.Any], str]) -> dict:
    """Copy gateway settings while masking token-like values."""
    out = dict(gateway)
    if "token" in out:
        out["token"] = masker(out["token"])
    if isinstance(out.get("headers"), dict):
        out["headers"] = {
            name: (masker(value)
                   if any(part in name.lower() for part in ("auth", "token", "key", "secret"))
                   else value)
            for name, value in out["headers"].items()
        }
    return out


def _split_ref(ref: str) -> tuple[str, str]:
    """'openai:text-embedding-3-small' -> ('openai', 'text-embedding-3-small').
    The console's `options` lists use this shape; a bare name is a model with
    no provider named."""
    provider, sep, model = str(ref or "").partition(":")
    return (provider.strip().lower(), model.strip()) if sep else ("", provider.strip())


def _resolve(cfg: dict) -> tuple[str, str]:
    """Return exactly the provider/model pair stored by the admin.

    Older rows may contain `default` or option-catalog metadata. Those fields
    are deliberately not executable configuration.
    """
    return (str(cfg.get("provider") or "").strip().lower(),
            str(cfg.get("model") or "").strip())


def _api_key(provider: str) -> str:
    llm_cfg, _ = _settings()
    keys = llm_cfg.get("keys")
    return str(keys.get(provider) or "").strip() if isinstance(keys, dict) else ""


def gateway_config() -> dict[str, t.Any]:
    """Merged deployment/workspace gateway config, with bounded retry policy."""
    llm_cfg, _ = _settings()
    stored = llm_cfg.get("gateway") if isinstance(llm_cfg.get("gateway"), dict) else {}
    cfg: dict[str, t.Any] = {
        "base_url": config.get("llm_gateway", "base_url") or "",
        "token": config.get("llm_gateway", "token") or _api_key("gateway"),
        "headers": config.get("llm_gateway", "headers") or {},
        "metadata": config.get("llm_gateway", "metadata") or {},
        "model_header": config.get("llm_gateway", "model_header") or "",
        "max_retries": config.get("llm_gateway", "max_retries", 2),
        "compatibility": config.get("llm_gateway", "compatibility", "openai"),
    }
    cfg.update(stored)
    cfg["base_url"] = str(cfg.get("base_url") or "").rstrip("/")
    cfg["token"] = str(cfg.get("token") or _api_key("gateway") or "")
    cfg["headers"] = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
    cfg["metadata"] = cfg.get("metadata") if isinstance(cfg.get("metadata"), dict) else {}
    cfg["compatibility"] = str(cfg.get("compatibility") or "").strip().lower()
    try:
        cfg["max_retries"] = min(5, max(0, int(cfg.get("max_retries", 2))))
    except (TypeError, ValueError):
        cfg["max_retries"] = 2
    return cfg


def _gateway_headers(cfg: dict[str, t.Any], model: str) -> dict[str, str]:
    headers = {str(k): str(v).replace("{model}", model)
               for k, v in cfg["headers"].items()
               if str(k).lower() not in {"host", "content-length"}}
    token = cfg.get("token") or ""
    if token:
        auth_header = str(cfg.get("auth_header") or "Authorization")
        auth_scheme = str(cfg.get("auth_scheme") or "Bearer").strip()
        headers[auth_header] = f"{auth_scheme} {token}".strip()
    model_header = str(cfg.get("model_header") or "").strip()
    if model_header:
        headers[model_header] = model
    request_id, correlation_id = observability.request_context()
    headers["X-Request-ID"] = request_id
    headers["X-Correlation-ID"] = correlation_id
    return headers


def _gateway_payload(payload: dict, cfg: dict[str, t.Any]) -> dict:
    if cfg.get("compatibility") == "deepseek":
        return payload
    metadata = cfg.get("metadata") or {}
    return {**payload, **({"metadata": metadata} if metadata else {})}


def _completion_limit(gateway: dict[str, t.Any] | None, tokens: int) -> dict[str, int]:
    """Provider-compatible completion limit without weakening generic gateways."""
    key = "max_tokens" if gateway and gateway.get("compatibility") == "deepseek" \
        else "max_completion_tokens"
    return {key: tokens}


def _gateway_config_error(cfg: dict[str, t.Any]) -> str:
    base = str(cfg.get("base_url") or "")
    if not base:
        return "LLM gateway base URL is not configured"
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        return "LLM gateway base URL must be an http(s) URL without embedded credentials"
    if cfg.get("compatibility") not in {"openai", "deepseek"}:
        return "LLM gateway compatibility must be configured as openai or deepseek"
    return ""


def ollama_host() -> str:
    """The ollama daemon's base URL: mari.toml / MARI_OLLAMA_HOST, then the
    default. This is the read that makes MARI_OLLAMA_HOST mean something."""
    return str(config.get("ollama", "host") or DEFAULT_OLLAMA).rstrip("/")


def embedding_model() -> tuple[str, str]:
    provider = str(config.get("models", "embedding_provider") or "").strip().lower()
    model = str(config.get("models", "embedding_model") or "").strip()
    if provider or model:
        return provider, model
    _, embed_cfg = _settings()
    return _resolve(embed_cfg)


def generation_model() -> tuple[str, str]:
    provider = str(config.get("models", "generation_provider") or "").strip().lower()
    model = str(config.get("models", "generation_model") or "").strip()
    if provider or model:
        return provider, model
    llm_cfg, _ = _settings()
    return _resolve(llm_cfg)


# ————————————————— HTTP —————————————————


def _retry_delay(headers: t.Any, attempt: int) -> float:
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            return min(30.0, max(0.0, float(raw)))
        except (TypeError, ValueError):
            try:
                target = email.utils.parsedate_to_datetime(str(raw)).timestamp()
                return min(30.0, max(0.0, target - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(4.0, 0.25 * (2 ** attempt))


def _error_detail(host: str, code: int, reason: object, *, gateway: bool) -> str:
    # Provider bodies can echo prompts, request headers, or tokens. The status
    # class is actionable without reflecting any untrusted body into UI/logs.
    if code in (401, 403):
        detail = "authentication or authorization failed"
    elif code == 429:
        detail = "rate limited"
    elif code >= 500:
        detail = "upstream service unavailable"
    elif gateway:
        detail = "request rejected"
    else:
        detail = str(reason or "request rejected")[:120]
    prefix = "LLM gateway" if gateway else host
    return f"{prefix} returned HTTP {code}: {detail}"


def _record_response_usage(out: t.Any, provider: str, model: str) -> None:
    if not isinstance(out, dict):
        return
    usage = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    cost = out.get("cost", usage.get("cost"))
    observability.record_llm_usage(provider, model, usage, cost)


def _post(url: str, payload: dict | None, headers: dict | None = None,
          timeout: float = 120.0, *, provider_name: str = "",
          max_retries: int = 0, method: str = "POST") -> dict | None:
    started = time.perf_counter()
    host = urllib.parse.urlsplit(url).netloc
    provider = provider_name or ("openai" if "openai" in host else "anthropic" if "anthropic" in host else "ollama")
    operation = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] or "request"
    success = False
    try:
        for attempt in range(max_retries + 1):
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode() if payload is not None else None,
                    headers={"Content-Type": "application/json", **(headers or {})}, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    out = json.loads(resp.read())
                _ok()
                success = True
                return out
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                if retryable and attempt < max_retries:
                    e.close()
                    time.sleep(_retry_delay(e.headers, attempt))
                    continue
                _fail(_error_detail(host, e.code, e.reason, gateway=provider == "gateway"))
                e.close()
                return None
            except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
                if attempt < max_retries:
                    time.sleep(_retry_delay(None, attempt))
                    continue
                reason = getattr(e, "reason", e)
                prefix = "LLM gateway" if provider == "gateway" else host
                _fail(f"cannot reach {prefix}: {type(reason).__name__}")
                return None
            except json.JSONDecodeError:
                prefix = "LLM gateway" if provider == "gateway" else host
                _fail(f"{prefix} returned an invalid JSON response")
                return None
            except (TypeError, ValueError):
                _fail("LLM provider endpoint or request configuration is invalid")
                return None
    finally:
        observability.record_llm(operation, provider, success, time.perf_counter() - started)
    return None


def _stream(url: str, payload: dict, headers: dict | None = None,
            timeout: float = 180.0, *, provider_name: str = "",
            max_retries: int = 0) -> t.Iterator[str]:
    """Yield raw response lines. Yields nothing when the endpoint is down —
    every caller of chat_stream already renders "no model available" for an
    empty stream."""
    started = time.perf_counter()
    host = urllib.parse.urlsplit(url).netloc
    provider = provider_name or ("openai" if "openai" in host else "anthropic" if "anthropic" in host else "ollama")
    operation = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] or "stream"
    success = False
    try:
        for attempt in range(max_retries + 1):
            yielded = False
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", **(headers or {})})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    for line in resp:
                        yielded = True
                        yield line.decode("utf-8", "replace")
                _ok()
                success = True
                return
            except urllib.error.HTTPError as e:
                if not yielded and (e.code == 429 or e.code >= 500) and attempt < max_retries:
                    e.close()
                    time.sleep(_retry_delay(e.headers, attempt))
                    continue
                _fail(_error_detail(host, e.code, e.reason, gateway=provider == "gateway"))
                e.close()
                return
            except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
                if not yielded and attempt < max_retries:
                    time.sleep(_retry_delay(None, attempt))
                    continue
                prefix = "LLM gateway" if provider == "gateway" else host
                _fail(f"cannot stream from {prefix}: {type(getattr(e, 'reason', e)).__name__}")
                return
            except (TypeError, ValueError):
                _fail("LLM provider endpoint or request configuration is invalid")
                return
    finally:
        observability.record_llm(operation, provider, success, time.perf_counter() - started)


# ————————————————— embeddings —————————————————


def _sentence_model(model: str) -> t.Any:
    with _sentence_models_lock:
        loaded = _sentence_models.get(model)
        if loaded is None:
            from sentence_transformers import SentenceTransformer
            loaded = SentenceTransformer(
                model,
                cache_folder=str(config.get("sentence_transformers", "cache_dir") or "") or None,
            )
            _sentence_models[model] = loaded
        return loaded


def embed(text: str) -> list[float] | None:
    """A vector for `text`, or None with `last_error()` explaining why.

    The vector is always EMBED_DIMS long, because that is the width of the
    column it goes into. A provider that cannot be asked for that width is
    refused here rather than at INSERT time, where the failure would read as a
    database error instead of a configuration one."""
    provider, model = embedding_model()
    body = text[:4000]

    if not provider or not model:
        _fail("embedding provider and model must both be configured")
        return None

    if provider == "ollama":
        out = _post(f"{ollama_host()}/api/embeddings",
                    {"model": model, "prompt": body}, timeout=30.0)
        vec = out.get("embedding") if out else None
    elif provider == "sentence-transformers":
        try:
            encoded = _sentence_model(model).encode(
                body, normalize_embeddings=True, convert_to_numpy=True)
            vec = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
        except Exception as exc:  # noqa: BLE001 — model/cache/runtime boundary
            _fail(f"sentence-transformers model {model!r} failed ({type(exc).__name__})")
            return None
    elif provider in ("openai", "gateway"):
        gateway = gateway_config() if provider == "gateway" else None
        key = (gateway or {}).get("token") or _api_key("openai")
        if provider == "openai" and not key:
            _fail(f"settings.embedding names the {provider} provider but no credential is set")
            return None
        payload: dict = {"model": model, "input": body}
        if model.startswith("text-embedding-3"):
            # These models serve a requested width directly, which is the only
            # reason an OpenAI embedding can fit a vector(768) column at all.
            payload["dimensions"] = EMBED_DIMS
        base = gateway["base_url"] if gateway else OPENAI_BASE
        if gateway and (config_error := _gateway_config_error(gateway)):
            _fail(config_error)
            return None
        headers = _gateway_headers(gateway, model) if gateway else {"Authorization": f"Bearer {key}"}
        if gateway:
            payload = _gateway_payload(payload, gateway)
        out = _post(f"{base}/embeddings", payload, headers, timeout=30.0,
                    provider_name=provider, max_retries=gateway["max_retries"] if gateway else 0)
        _record_response_usage(out, provider, model)
        data = (out or {}).get("data") or []
        vec = data[0].get("embedding") if data else None
    else:
        _fail(f"settings.embedding names provider {provider!r}, which has no embedding "
              f"endpoint here (supported: ollama, sentence-transformers, openai, gateway)")
        return None

    if not vec:
        if not last_error():
            _fail(f"{provider} returned no embedding for model {model!r}")
        return None
    if len(vec) != EMBED_DIMS:
        _fail(f"model {model!r} returned a {len(vec)}-dimension vector; this index "
              f"stores {EMBED_DIMS} (change the model, or migrate the column)")
        return None
    _ok()
    return vec


# ————————————————— generation —————————————————


def generate(prompt: str, system: str = "", timeout: float = 120.0) -> str | None:
    """One completion, or None with `last_error()` explaining why."""
    provider, model = generation_model()

    if not provider or not model:
        _fail("generation provider and model must both be configured")
        return None

    if provider == "ollama":
        out = _post(f"{ollama_host()}/api/generate",
                    {"model": model, "prompt": prompt, "system": system, "stream": False,
                     "options": {"temperature": 0.3, "num_predict": 700}},
                    timeout=timeout)
        return out.get("response", "").strip() if out else None

    if provider in ("openai", "gateway"):
        gateway = gateway_config() if provider == "gateway" else None
        key = (gateway or {}).get("token") or _api_key("openai")
        if provider == "openai" and not key:
            _fail("settings.llm names the openai provider but no OpenAI API key is set")
            return None
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        base = gateway["base_url"] if gateway else OPENAI_BASE
        if gateway and (config_error := _gateway_config_error(gateway)):
            _fail(config_error)
            return None
        payload = {"model": model, "messages": messages, **_completion_limit(gateway, 700)}
        headers = _gateway_headers(gateway, model) if gateway else {"Authorization": f"Bearer {key}"}
        if gateway:
            payload = _gateway_payload(payload, gateway)
        out = _post(f"{base}/chat/completions", payload, headers, timeout=timeout,
                    provider_name=provider, max_retries=gateway["max_retries"] if gateway else 0)
        _record_response_usage(out, provider, model)
        choices = (out or {}).get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else None
        return text.strip() if text else None

    if provider == "anthropic":
        key = _api_key("anthropic")
        if not key:
            _fail("settings.llm names the anthropic provider but no Anthropic API key is "
                  "set (Settings → Models)")
            return None
        payload = {"model": model, "max_tokens": 1024,
                   "messages": [{"role": "user", "content": prompt}]}
        if system:
            payload["system"] = system
        out = _post(f"{ANTHROPIC_BASE}/messages", payload,
                    {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
                    timeout=timeout)
        if not out:
            return None
        if out.get("stop_reason") == "refusal":
            _fail("the model declined this request")
            return None
        text = "".join(b.get("text", "") for b in (out.get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "text")
        return text.strip() or None

    _fail(f"settings.llm names provider {provider!r}, which is not one this server can "
          f"call (supported: ollama, local, openai, anthropic, gateway)")
    return None


def generate_json(prompt: str, system: str = "", timeout: float = 120.0) -> t.Any | None:
    """Ask for JSON and parse leniently (strip code fences, find first bracket).

    `timeout` is exposed because callers that make many of these in a row need
    to bound the total, and a 120-second default multiplied by eight documents
    is sixteen minutes."""
    raw = generate(prompt + "\n\nRespond with ONLY valid JSON, no prose.", system, timeout)
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = raw.find(opener), raw.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(raw[i : j + 1])
            except json.JSONDecodeError:
                continue
    _fail("the model's answer did not contain parseable JSON")
    return None


# ————————————————— chat streaming —————————————————


def chat_stream(messages: list[dict], system: str = "") -> t.Iterator[str]:
    """Yield response tokens; yields nothing if the configured model is down."""
    provider, model = generation_model()

    if not provider or not model:
        _fail("generation provider and model must both be configured")
        return

    if provider == "ollama":
        payload = {"model": model,
                   "messages": ([{"role": "system", "content": system}] if system else []) + messages,
                   "stream": True,
                   "options": {"temperature": 0.4, "num_predict": 800}}
        for line in _stream(f"{ollama_host()}/api/chat", payload):
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            token = chunk.get("message", {}).get("content", "")
            if token:
                yield token
            if chunk.get("done"):
                return
        return

    if provider in ("openai", "gateway"):
        gateway = gateway_config() if provider == "gateway" else None
        key = (gateway or {}).get("token") or _api_key("openai")
        if provider == "openai" and not key:
            _fail("settings.llm names the openai provider but no OpenAI API key is set")
            return
        payload = {"model": model, "stream": True, **_completion_limit(gateway, 800),
                   "messages": ([{"role": "system", "content": system}] if system else []) + messages}
        base = gateway["base_url"] if gateway else OPENAI_BASE
        if gateway and (config_error := _gateway_config_error(gateway)):
            _fail(config_error)
            return
        headers = _gateway_headers(gateway, model) if gateway else {"Authorization": f"Bearer {key}"}
        if gateway:
            payload = _gateway_payload(payload, gateway)
            payload["stream_options"] = {"include_usage": True}
        for line in _stream(f"{base}/chat/completions", payload, headers,
                            provider_name=provider,
                            max_retries=gateway["max_retries"] if gateway else 0):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            _record_response_usage(chunk, provider, model)
            for choice in chunk.get("choices") or []:
                token = (choice.get("delta") or {}).get("content")
                if token:
                    yield token
        return

    if provider == "anthropic":
        key = _api_key("anthropic")
        if not key:
            _fail("settings.llm names the anthropic provider but no Anthropic API key is set")
            return
        payload = {"model": model, "max_tokens": 1024, "stream": True, "messages": messages}
        if system:
            payload["system"] = system
        for line in _stream(f"{ANTHROPIC_BASE}/messages", payload,
                            {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}):
            if not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = chunk.get("type")
            if kind == "content_block_delta":
                token = (chunk.get("delta") or {}).get("text")
                if token:
                    yield token
            elif kind == "message_stop":
                return
        return

    _fail(f"settings.llm names provider {provider!r}, which is not one this server can call")


def gateway_health(timeout: float = 10.0) -> dict[str, t.Any]:
    """Credentialed, prompt-free gateway test against the OpenAI models API."""
    cfg = gateway_config()
    if config_error := _gateway_config_error(cfg):
        return {"ok": False, "detail": config_error, "models": 0}
    started = time.perf_counter()
    out = _post(f"{cfg['base_url']}/models", None, _gateway_headers(cfg, "health-check"),
                timeout=timeout, provider_name="gateway", max_retries=cfg["max_retries"], method="GET")
    if out is None:
        return {"ok": False, "detail": last_error() or "LLM gateway health check failed", "models": 0,
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    models = out.get("data") if isinstance(out, dict) else []
    return {"ok": True, "detail": "LLM gateway is reachable and authenticated",
            "models": len(models) if isinstance(models, list) else 0,
            "latency_ms": round((time.perf_counter() - started) * 1000)}


def model_catalog(*, refresh: bool = False) -> dict[str, t.Any]:
    """Models proven available by provider APIs plus the exact active choices.

    Nothing is inferred from a name. Ollama's `/api/tags` finds installed
    models and `/api/show` classifies their declared capabilities. An
    OpenAI-compatible gateway's `/models` list is generation-only unless the
    active embedding selection explicitly names one of its models, because the
    standard model object does not advertise embedding capability.
    """
    now = time.monotonic()
    with _catalog_lock:
        cached = _catalog_cache.get("value")
        if not refresh and cached is not None and now - float(_catalog_cache["at"]) < 30.0:
            return dict(cached)

    embedding = embedding_model()
    generation = generation_model()
    embedding_options: set[str] = set()
    generation_options: set[str] = set()
    errors: dict[str, str] = {}

    if all(embedding):
        embedding_options.add(f"{embedding[0]}:{embedding[1]}")
    if all(generation):
        generation_options.add(f"{generation[0]}:{generation[1]}")

    tags = _post(f"{ollama_host()}/api/tags", None, timeout=2.0, method="GET")
    if tags is None:
        errors["ollama"] = last_error() or "Ollama model discovery failed"
    else:
        for item in (tags.get("models") or [])[:50]:
            name = str(item.get("name") or item.get("model") or "").strip() \
                if isinstance(item, dict) else ""
            if not name:
                continue
            shown = _post(f"{ollama_host()}/api/show", {"model": name}, timeout=2.0)
            if shown is None:
                errors["ollama"] = last_error() or "Ollama capability discovery failed"
                continue
            capabilities = set(shown.get("capabilities") or [])
            if "embedding" in capabilities:
                embedding_options.add(f"ollama:{name}")
            if capabilities.intersection({"completion", "tools", "vision"}):
                generation_options.add(f"ollama:{name}")

    gateway = gateway_config()
    if gateway.get("base_url"):
        if config_error := _gateway_config_error(gateway):
            errors["gateway"] = config_error
        else:
            listed = _post(f"{gateway['base_url']}/models", None,
                           _gateway_headers(gateway, "model-catalog"), timeout=3.0,
                           provider_name="gateway", max_retries=gateway["max_retries"],
                           method="GET")
            if listed is None:
                errors["gateway"] = last_error() or "LLM gateway model discovery failed"
            else:
                for item in listed.get("data") or []:
                    model_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
                    if model_id:
                        generation_options.add(f"gateway:{model_id}")

    value = {
        "embedding": sorted(embedding_options),
        "generation": sorted(generation_options),
        "errors": errors,
    }
    with _catalog_lock:
        _catalog_cache.update({"at": now, "value": value})
    return dict(value)

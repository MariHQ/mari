"""Mari Cloud — the model client: embeddings, generation, chat streaming.

DESIGN.md §5/§10: the embedding model and the LLM are configurable. This module
now actually reads that configuration.

It used to hardcode `http://localhost:11434`, `nomic-embed-text` and `gemma3:4b`
while its own docstring claimed otherwise, which made Settings → Models a page
that wrote a row nobody read (DEAD-1) and made `MARI_OLLAMA_HOST` — set by the
compose file precisely because `localhost` inside a container is the container —
a variable with no reader, so the compose deployment could never reach ollama at
all (DEAD-2). Both settings rows are read here now, on every call, through a
short-lived cache.

Resolution order, per capability:

  1. `settings.llm` / `settings.embedding` — what an admin chose in the console.
  2. `mari.toml` / `MARI_OLLAMA_HOST` (config.py) — what the deployment provides.
  3. The shipped defaults below.

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
import threading
import time
import typing as t
import urllib.error
import urllib.parse
import urllib.request

import config

# Shipped defaults — the last resort, after settings and after mari.toml.
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


def _split_ref(ref: str) -> tuple[str, str]:
    """'openai:text-embedding-3-small' -> ('openai', 'text-embedding-3-small').
    The console's `options` lists use this shape; a bare name is a model with
    no provider named."""
    provider, sep, model = str(ref or "").partition(":")
    return (provider.strip().lower(), model.strip()) if sep else ("", provider.strip())


def _resolve(cfg: dict, default_provider: str, default_model: str) -> tuple[str, str]:
    """(provider, model) from a settings row. Explicit `provider`/`model` fields
    win; otherwise the row's `default` ('provider:model') is parsed; otherwise
    the shipped defaults. An empty model field never overrides a real default —
    a blank box in the console means "unset", not "use the empty model"."""
    provider = str(cfg.get("provider") or "").strip().lower()
    model = str(cfg.get("model") or "").strip()
    if not provider or not model:
        ref_provider, ref_model = _split_ref(cfg.get("default") or "")
        provider = provider or ref_provider
        model = model or ref_model
    return provider or default_provider, model or default_model


def _api_key(provider: str) -> str:
    llm_cfg, _ = _settings()
    keys = llm_cfg.get("keys")
    return str(keys.get(provider) or "").strip() if isinstance(keys, dict) else ""


def ollama_host() -> str:
    """The ollama daemon's base URL: mari.toml / MARI_OLLAMA_HOST, then the
    default. This is the read that makes MARI_OLLAMA_HOST mean something."""
    return str(config.get("ollama", "host") or DEFAULT_OLLAMA).rstrip("/")


def embedding_model() -> tuple[str, str]:
    _, embed_cfg = _settings()
    return _resolve(embed_cfg, "ollama",
                    str(config.get("ollama", "embed_model") or DEFAULT_EMBED_MODEL))


def generation_model() -> tuple[str, str]:
    llm_cfg, _ = _settings()
    return _resolve(llm_cfg, "ollama",
                    str(config.get("ollama", "gen_model") or DEFAULT_GEN_MODEL))


# ————————————————— HTTP —————————————————


def _post(url: str, payload: dict, headers: dict | None = None,
          timeout: float = 120.0) -> dict | None:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read())
        _ok()
        return out
    except urllib.error.HTTPError as e:
        # The provider said why. Quote it — a status code alone sends whoever
        # reads the log to the wrong place.
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            detail = ""
        _fail(f"{urllib.parse.urlsplit(url).netloc} returned HTTP {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        _fail(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {e.reason}")
    except TimeoutError:
        _fail(f"{urllib.parse.urlsplit(url).netloc} did not answer within {timeout:.0f}s")
    except json.JSONDecodeError as e:
        _fail(f"{urllib.parse.urlsplit(url).netloc} returned something that is not JSON: {e}")
    return None


def _stream(url: str, payload: dict, headers: dict | None = None,
            timeout: float = 180.0) -> t.Iterator[str]:
    """Yield raw response lines. Yields nothing when the endpoint is down —
    every caller of chat_stream already renders "no model available" for an
    empty stream."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                yield line.decode("utf-8", "replace")
        _ok()
    except (urllib.error.URLError, TimeoutError) as e:
        _fail(f"cannot stream from {urllib.parse.urlsplit(url).netloc}: {e}")
        return


# ————————————————— embeddings —————————————————


def embed(text: str) -> list[float] | None:
    """A vector for `text`, or None with `last_error()` explaining why.

    The vector is always EMBED_DIMS long, because that is the width of the
    column it goes into. A provider that cannot be asked for that width is
    refused here rather than at INSERT time, where the failure would read as a
    database error instead of a configuration one."""
    provider, model = embedding_model()
    body = text[:4000]

    if provider in ("ollama", "local", ""):
        out = _post(f"{ollama_host()}/api/embeddings",
                    {"model": model, "prompt": body}, timeout=30.0)
        vec = out.get("embedding") if out else None
    elif provider == "openai":
        key = _api_key("openai")
        if not key:
            _fail("settings.embedding names the openai provider but no OpenAI API key "
                  "is set (Settings → Models)")
            return None
        payload: dict = {"model": model, "input": body}
        if model.startswith("text-embedding-3"):
            # These models serve a requested width directly, which is the only
            # reason an OpenAI embedding can fit a vector(768) column at all.
            payload["dimensions"] = EMBED_DIMS
        out = _post(f"{OPENAI_BASE}/embeddings", payload,
                    {"Authorization": f"Bearer {key}"}, timeout=30.0)
        data = (out or {}).get("data") or []
        vec = data[0].get("embedding") if data else None
    else:
        _fail(f"settings.embedding names provider {provider!r}, which has no embedding "
              f"endpoint here (supported: ollama, local, openai)")
        return None

    if not vec:
        if not last_error():
            _fail(f"{provider or 'ollama'} returned no embedding for model {model!r}")
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

    if provider in ("ollama", "local", ""):
        out = _post(f"{ollama_host()}/api/generate",
                    {"model": model, "prompt": prompt, "system": system, "stream": False,
                     "options": {"temperature": 0.3, "num_predict": 700}},
                    timeout=timeout)
        return out.get("response", "").strip() if out else None

    if provider == "openai":
        key = _api_key("openai")
        if not key:
            _fail("settings.llm names the openai provider but no OpenAI API key is set "
                  "(Settings → Models)")
            return None
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        out = _post(f"{OPENAI_BASE}/chat/completions",
                    {"model": model, "messages": messages, "max_completion_tokens": 700},
                    {"Authorization": f"Bearer {key}"}, timeout=timeout)
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
          f"call (supported: ollama, local, openai, anthropic)")
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

    if provider in ("ollama", "local", ""):
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

    if provider == "openai":
        key = _api_key("openai")
        if not key:
            _fail("settings.llm names the openai provider but no OpenAI API key is set")
            return
        payload = {"model": model, "stream": True, "max_completion_tokens": 800,
                   "messages": ([{"role": "system", "content": system}] if system else []) + messages}
        for line in _stream(f"{OPENAI_BASE}/chat/completions", payload,
                            {"Authorization": f"Bearer {key}"}):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
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

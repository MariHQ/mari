"""Ollama client for Mari Cloud — embeddings + generation with graceful fallback.

DESIGN.md §5/§10: the embedding model and LLM are configurable (settings table);
this module reads them and talks to a local ollama daemon. Every call degrades
to a deterministic fallback so the demo stays functional without ollama.
"""

from __future__ import annotations

import json
import typing as t
import urllib.error
import urllib.request

OLLAMA = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
GEN_MODEL = "gemma3:4b"


def _post(path: str, payload: dict, timeout: float = 120.0) -> dict | None:
    req = urllib.request.Request(
        f"{OLLAMA}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def embed(text: str) -> list[float] | None:
    out = _post("/api/embeddings", {"model": EMBED_MODEL, "prompt": text[:4000]}, timeout=30.0)
    return out.get("embedding") if out else None


def generate(prompt: str, system: str = "", timeout: float = 120.0) -> str | None:
    out = _post(
        "/api/generate",
        {"model": GEN_MODEL, "prompt": prompt, "system": system, "stream": False,
         "options": {"temperature": 0.3, "num_predict": 700}},
        timeout=timeout,
    )
    return out.get("response", "").strip() if out else None


def generate_json(prompt: str, system: str = "") -> t.Any | None:
    """Ask for JSON and parse leniently (strip code fences, find first bracket)."""
    raw = generate(prompt + "\n\nRespond with ONLY valid JSON, no prose.", system)
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
    return None


def chat_stream(messages: list[dict], system: str = "") -> t.Iterator[str]:
    """Yield response tokens from ollama /api/chat; yields nothing if ollama is down."""
    payload = {
        "model": GEN_MODEL,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "stream": True,
        "options": {"temperature": 0.4, "num_predict": 800},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180.0) as resp:
            for line in resp:
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token
                if chunk.get("done"):
                    return
    except (urllib.error.URLError, TimeoutError):
        return

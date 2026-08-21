"""Resolve the one explicitly configured knowledge substrate."""

from __future__ import annotations

import threading
import typing as t

from mari_components.substrates import KnowledgeSubstrate

from mari_server import settings
from mari_server.persistence.postgres import settings as settings_store

from .errors import SubstrateConfigurationError
from .native import NativeSubstrate
from .onyx import OnyxSubstrate

_instance: KnowledgeSubstrate | None = None
_instance_key: tuple[t.Any, ...] | None = None
_lock = threading.Lock()


def effective_configuration() -> dict[str, t.Any]:
    """Return the explicitly selected deployment or workspace configuration."""
    deployment = {
        "provider": str(settings.get("knowledge_substrate", "provider", "native")),
        "url": str(settings.get("knowledge_substrate", "url", "")),
        "api_key": str(settings.get("knowledge_substrate", "api_key", "")),
        "timeout_seconds": int(settings.get("knowledge_substrate", "timeout_seconds", 30)),
        "search_mode": str(settings.get("knowledge_substrate", "search_mode", "keyword")),
    }
    try:
        workspace = settings_store.value("knowledge_substrate")
    except RuntimeError:
        workspace = None
    if not isinstance(workspace, dict):
        return deployment
    return {
        "provider": str(workspace.get("provider") or "native"),
        "url": str(workspace.get("url") or ""),
        "api_key": str(workspace.get("api_key") or ""),
        "timeout_seconds": int(workspace.get("timeout_seconds") or 30),
        "search_mode": str(workspace.get("search_mode") or "keyword"),
    }


def configured_substrate() -> KnowledgeSubstrate:
    global _instance, _instance_key
    cfg = effective_configuration()
    key = tuple(cfg[name] for name in (
        "provider", "url", "api_key", "timeout_seconds", "search_mode"))
    if _instance is not None and _instance_key == key:
        return _instance
    with _lock:
        if _instance is not None and _instance_key == key:
            return _instance
        provider = str(cfg["provider"]).strip().lower()
        if provider == "native":
            _instance = NativeSubstrate()
        elif provider == "onyx":
            _instance = OnyxSubstrate(
                str(cfg["url"]), str(cfg["api_key"]),
                timeout=float(cfg["timeout_seconds"]),
                search_mode=str(cfg["search_mode"]),
            )
        else:
            raise SubstrateConfigurationError(
                f"Unknown knowledge substrate {provider!r}; expected 'native' or 'onyx'."
            )
        _instance_key = key
        return _instance


def reset_configured_substrate() -> None:
    """Drop the cached adapter after an explicit configuration reload."""
    global _instance, _instance_key
    with _lock:
        _instance = None
        _instance_key = None

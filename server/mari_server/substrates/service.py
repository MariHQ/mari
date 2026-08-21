"""Resolve the one explicitly configured knowledge substrate."""

from __future__ import annotations

import threading

from mari_components.substrates import KnowledgeSubstrate

from mari_server import settings

from .errors import SubstrateConfigurationError
from .native import NativeSubstrate
from .onyx import OnyxSubstrate

_instance: KnowledgeSubstrate | None = None
_lock = threading.Lock()


def configured_substrate() -> KnowledgeSubstrate:
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is not None:
            return _instance
        provider = str(settings.get("knowledge_substrate", "provider", "native")).strip().lower()
        if provider == "native":
            _instance = NativeSubstrate()
        elif provider == "onyx":
            _instance = OnyxSubstrate(
                str(settings.get("knowledge_substrate", "url", "")),
                str(settings.get("knowledge_substrate", "api_key", "")),
                timeout=float(settings.get("knowledge_substrate", "timeout_seconds", 30)),
                search_mode=str(settings.get("knowledge_substrate", "search_mode", "keyword")),
            )
        else:
            raise SubstrateConfigurationError(
                f"Unknown knowledge substrate {provider!r}; expected 'native' or 'onyx'."
            )
        return _instance


def reset_configured_substrate() -> None:
    """Drop the cached adapter after an explicit configuration reload."""
    global _instance
    with _lock:
        _instance = None

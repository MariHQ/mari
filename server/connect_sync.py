"""Compatibility facade for connector ingestion infrastructure."""

from mari_server.infrastructure.connector_runtime import (
    FALLBACK_SECRET_KEYS,
    INTERNAL_KEYS,
    MASK,
    _sync_worker,
    deletion_ids,
    masked_config,
    provider_key_of,
    secret_fields,
)

__all__ = [
    "FALLBACK_SECRET_KEYS", "INTERNAL_KEYS", "MASK", "_sync_worker",
    "deletion_ids", "masked_config", "provider_key_of", "secret_fields",
]

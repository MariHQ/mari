"""Mari — configuration (DESIGN.md §24).

Reads mari.toml (path from MARI_CONFIG, default ./mari.toml or ../mari.toml),
then lets environment variables override. Everything a cloud deployment needs
lives here so `docker compose up` works with one file.
"""

from __future__ import annotations

import json
import os
import pathlib
import tomllib
import typing as t

_DEFAULTS: dict[str, t.Any] = {
    "database": {"url": "postgresql://localhost/mari_cloud"},
    "server": {"host": "0.0.0.0", "port": 8000,
               "cors_origins": ["http://localhost:5173"], "trusted_proxies": []},
    "ollama": {"host": "http://localhost:11434", "embed_model": "nomic-embed-text", "gen_model": "gemma3:4b"},
    # Optional deployment-owned selection. Provider and model are an atomic
    # pair; a partial pair is an explicit configuration error in llm.py.
    "models": {"generation_provider": "", "generation_model": "",
               "embedding_provider": "", "embedding_model": ""},
    "sentence_transformers": {
        "cache_dir": "var/mari/cache/sentence-transformers",
    },
    "llm_gateway": {
        "base_url": "", "token": "", "headers": {}, "metadata": {},
        "model_header": "", "max_retries": 2, "compatibility": "openai",
    },
    "auth": {
        "session_days": 14,
        # The demo bypass (POST /auth/bypass) signs anyone in as the workspace
        # admin without a credential. It is a real, documented feature, so it
        # stays — but it defaults OFF: a default that hands out admin is not a
        # default, it is an outage waiting for someone to find the port. Turn
        # it on deliberately with MARI_AUTH_BYPASS=true for demo instances.
        "bypass_enabled": False,
        # A second, explicit gate: even when bypass_enabled is true the auth
        # endpoint remains closed unless this instance is declared a local
        # development/demo environment and has exactly one active project.
        "bypass_dev_mode": False,
        # Open sign-up. Off by default: an account is all the GraphQL surface
        # asks for, so a workspace is invite-only until it says otherwise.
        # Invited members can always register — see POST /auth/register.
        "registration_enabled": False,
        "github_client_id": "", "github_client_secret": "",
        "google_client_id": "", "google_client_secret": "",
        "oauth_redirect_base": "http://localhost:8000",
        "app_url": "http://localhost:5173",
        "oidc_issuer": "", "oidc_client_id": "", "oidc_client_secret": "",
        "oidc_scopes": "openid email profile groups", "oidc_group_role_map": {},
        "scim_bearer_token": "",
    },
    "s3": {"bucket": "", "region": "", "endpoint_url": ""},
    "audit": {"languages": ["es", "fr"], "default_tag": "customer-facing"},
    "github": {"token": "", "webhook_secret": ""},
}


def _load_dotenv() -> None:
    """Tiny stdlib .env loader: KEY=VALUE lines from the repo-root .env (or ./.env),
    applied only for keys not already set in the environment. Never logs values."""
    here = pathlib.Path(__file__).resolve().parent
    for candidate in (here.parent / ".env", here / ".env", pathlib.Path(".env")):
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass
        break


def _load() -> dict:
    _load_dotenv()
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    for candidate in (os.environ.get("MARI_CONFIG"), "mari.toml", "../mari.toml"):
        if candidate and pathlib.Path(candidate).exists():
            with open(candidate, "rb") as f:
                loaded = tomllib.load(f)
            for section, values in loaded.items():
                cfg.setdefault(section, {}).update(values)
            break
    # env overrides (12-factor for the compose file)
    env_map = {
        "MARI_DB": ("database", "url"),
        "MARI_OLLAMA_HOST": ("ollama", "host"),
        "MARI_OLLAMA_EMBED_MODEL": ("ollama", "embed_model"),
        "MARI_OLLAMA_GEN_MODEL": ("ollama", "gen_model"),
        "MARI_LLM_PROVIDER": ("models", "generation_provider"),
        "MARI_LLM_MODEL": ("models", "generation_model"),
        "MARI_EMBEDDING_PROVIDER": ("models", "embedding_provider"),
        "MARI_EMBEDDING_MODEL": ("models", "embedding_model"),
        "MARI_SENTENCE_TRANSFORMERS_CACHE": ("sentence_transformers", "cache_dir"),
        "MARI_LLM_GATEWAY_URL": ("llm_gateway", "base_url"),
        "MARI_LLM_GATEWAY_TOKEN": ("llm_gateway", "token"),
        "MARI_LLM_GATEWAY_HEADERS": ("llm_gateway", "headers"),
        "MARI_LLM_GATEWAY_METADATA": ("llm_gateway", "metadata"),
        "MARI_LLM_GATEWAY_MODEL_HEADER": ("llm_gateway", "model_header"),
        "MARI_LLM_GATEWAY_RETRIES": ("llm_gateway", "max_retries"),
        "MARI_LLM_GATEWAY_COMPATIBILITY": ("llm_gateway", "compatibility"),
        "MARI_S3_BUCKET": ("s3", "bucket"),
        "MARI_S3_ENDPOINT_URL": ("s3", "endpoint_url"),
        "MARI_GITHUB_CLIENT_ID": ("auth", "github_client_id"),
        "MARI_GITHUB_CLIENT_SECRET": ("auth", "github_client_secret"),
        "MARI_GOOGLE_CLIENT_ID": ("auth", "google_client_id"),
        "MARI_GOOGLE_CLIENT_SECRET": ("auth", "google_client_secret"),
        "MARI_OAUTH_REDIRECT_BASE": ("auth", "oauth_redirect_base"),
        "MARI_APP_URL": ("auth", "app_url"),
        "MARI_OIDC_ISSUER": ("auth", "oidc_issuer"),
        "MARI_OIDC_CLIENT_ID": ("auth", "oidc_client_id"),
        "MARI_OIDC_CLIENT_SECRET": ("auth", "oidc_client_secret"),
        "MARI_OIDC_SCOPES": ("auth", "oidc_scopes"),
        "MARI_OIDC_GROUP_ROLE_MAP": ("auth", "oidc_group_role_map"),
        "MARI_SCIM_BEARER_TOKEN": ("auth", "scim_bearer_token"),
        "MARI_AUTH_BYPASS": ("auth", "bypass_enabled"),
        "MARI_AUTH_BYPASS_DEV_MODE": ("auth", "bypass_dev_mode"),
        "MARI_AUTH_REGISTRATION": ("auth", "registration_enabled"),
        "MARI_CORS_ORIGINS": ("server", "cors_origins"),
        "MARI_TRUSTED_PROXIES": ("server", "trusted_proxies"),
        "MARI_GITHUB_TOKEN": ("github", "token"),
        "MARI_GITHUB_WEBHOOK_SECRET": ("github", "webhook_secret"),
    }
    for env, (section, key) in env_map.items():
        if os.environ.get(env):
            value: t.Any = os.environ[env]
            if key in ("cors_origins", "trusted_proxies"):
                value = [o.strip() for o in value.split(",")]
            elif key in ("bypass_enabled", "bypass_dev_mode", "registration_enabled"):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            elif key in ("headers", "metadata", "oidc_group_role_map"):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    continue
                if not isinstance(value, dict):
                    continue
            elif key == "max_retries":
                try:
                    value = int(value)
                except ValueError:
                    continue
            cfg[section][key] = value
    return cfg


CONFIG = _load()


def get(section: str, key: str, default: t.Any = None) -> t.Any:
    return CONFIG.get(section, {}).get(key, default)

"""Mari Cloud — connector REST surface (CONNECTORS-CONTRACT.md).

GET  /connectors/catalog   → registry-driven catalog; field SPECS only, never
                             stored values; connected/sourceId from live sources.
POST /connectors/validate  → {ok, error} — the pre-connect "Test connection".
POST /connectors/connect   → validate first (honest 200 {error} on failure),
                             create the kind='connector' source, background
                             initial sync → {sourceId}.

Secrets live in sources.config jsonb and are never returned by any endpoint.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel

import connect_sync
import connectors
import flowengine
import ingest
from db import audit, exec_, q, q1

router = APIRouter(prefix="/connectors")

# Main-step order (contract): the rest appear under "Show all".
TOP8 = ["github", "slack", "upload", "website", "notion", "gdrive", "confluence", "jira"]

# Existing, fully-live connectors that are not provider modules: github has a
# dedicated repo-picker path (connectGithubRepo), upload posts to /onboard/upload.
BUILTIN = [
    {"key": "github", "name": "GitHub", "builtin": True,
     "blurb": "Markdown docs, issues, PRs and commit messages from your repos.",
     "fields": [], "docsUrl": "https://docs.github.com/en/authentication"},
    {"key": "upload", "name": "Upload", "builtin": True,
     "blurb": "Markdown and text files straight from your device.",
     "fields": [], "docsUrl": ""},
]

# Non-secret config fields that identify one instance of a provider — used to
# qualify sources.provider (UNIQUE) and the display name.
QUALIFIER_KEYS = ("start_url", "site_url", "base_url", "url", "workspace",
                  "subdomain", "site", "domain", "team", "space_key", "database_id")


class ProviderIn(BaseModel):
    provider: str
    config: dict = {}


def _field_specs(provider: dict) -> list[dict]:
    """Field SPECS only — never stored values."""
    return [{"key": f.get("key", ""), "label": f.get("label", ""),
             "secret": bool(f.get("secret")), "placeholder": f.get("placeholder", ""),
             "help": f.get("help", "")} for f in provider.get("fields", [])]


def _connected_map() -> dict[str, int]:
    """provider key → newest live source id."""
    out: dict[str, int] = {}
    for r in q("""SELECT id, kind, provider, config FROM sources
                  WHERE kind IN ('github', 'upload', 'connector') ORDER BY id"""):
        cfg = r["config"] if isinstance(r["config"], dict) else json.loads(r["config"] or "{}")
        if r["kind"] == "connector":
            out[connect_sync.provider_key_of(r["provider"], cfg)] = r["id"]
        else:
            out[r["kind"]] = r["id"]
    return out


@router.get("/catalog")
def catalog() -> list[dict]:
    connectors.REGISTRY.refresh()  # pick up provider modules added since startup
    entries: dict[str, dict] = {}
    for b in BUILTIN:
        entries[b["key"]] = dict(b)
    for e in connectors.REGISTRY.values():
        prov = e.get("provider")
        if not prov or e.get("error"):
            continue  # broken module: not real, so not in the catalog
        entries[prov["key"]] = {
            "key": prov["key"], "name": prov.get("name", prov["key"]),
            "blurb": prov.get("blurb", ""), "fields": _field_specs(prov),
            "docsUrl": prov.get("docs_url", ""), "builtin": False,
        }
    connected = _connected_map()
    for key, item in entries.items():
        item["connected"] = key in connected
        item["sourceId"] = connected.get(key)
    head = [entries[k] for k in TOP8 if k in entries]
    tail = sorted((v for k, v in entries.items() if k not in TOP8), key=lambda v: v["name"].lower())
    return head + tail


@router.post("/validate")
def validate(body: ProviderIn) -> dict:
    entry = connectors.REGISTRY.get(body.provider)
    if not entry or not entry.get("provider"):
        connectors.REGISTRY.refresh()
        entry = connectors.REGISTRY.get(body.provider)
    if not entry or not entry.get("provider"):
        return {"ok": False, "error": f"Unknown connector provider '{body.provider}'"}
    try:
        err = entry["validate"](body.config or {})
    except Exception as e:  # noqa: BLE001 — an honest error beats a 500
        err = f"{type(e).__name__}: {e}"
    return {"ok": err is None, "error": str(err) if err else ""}


def _qualifier(provider: dict, config: dict) -> str:
    """Short instance label from the first identifying non-secret field."""
    secret = {f["key"] for f in provider.get("fields", []) if f.get("secret")}
    for k in QUALIFIER_KEYS:
        v = (config.get(k) or "").strip() if isinstance(config.get(k), str) else ""
        if v and k not in secret:
            v = v.replace("https://", "").replace("http://", "").rstrip("/")
            return v[:48]
    return ""


@router.post("/connect")
def connect(body: ProviderIn) -> dict:
    check = validate(body)
    if not check["ok"]:
        return {"error": check["error"]}  # honest failure; no source row created
    entry = connectors.REGISTRY[body.provider]
    prov = entry["provider"]
    key = prov["key"]

    qual = _qualifier(prov, body.config or {})
    provider_col = f"{key}:{qual}" if qual else key
    display = f"{prov.get('name', key)} — {qual}" if qual else prov.get("name", key)
    if q1("SELECT id FROM sources WHERE kind = 'connector' AND provider = %s", (provider_col,)):
        return {"error": f"{display} is already connected"}

    cfg = dict(body.config or {})
    cfg.update({"provider_key": key, "cursor": "", "item_hashes": {},
                "last_sync_at": "", "last_error": ""})
    exec_("""INSERT INTO sources (provider, display_name, kind, status, stat_num, stat_unit,
                                  bars, config, docs_count, health)
             VALUES (%s, %s, 'connector', 'active', '0', 'docs', '{}', %s, 0, 'Syncing')""",
          (provider_col, display, json.dumps(cfg)))
    source_id = q1("SELECT id FROM sources WHERE provider = %s", (provider_col,))["id"]
    exec_("""INSERT INTO sync_events (provider, event, detail, at_label)
             VALUES (%s, %s, '', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
          (provider_col, f"connected: {display}"))
    audit("connected source", display)
    # every connected source gets a scheduled sync flow (Flows UI owns cadence)
    flowengine.ensure_sync_flow(source_id, display)
    ingest.start_sync(source_id)  # dispatches to connect_sync by kind
    return {"sourceId": source_id}

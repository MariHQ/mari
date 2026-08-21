"""Mari — connector REST surface (CONNECTORS-CONTRACT.md).

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
import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from mari_server.identity import routes as auth
from mari_server.identity import access
from mari_server.persistence.postgres import connector_sync
from mari_server.providers import connectors as component_connectors
from mari_server.automations import runtime as flowengine
from mari_server.sources import sync as ingest
from mari_server.persistence.postgres import sources as source_store
from mari_server.persistence.postgres.database import audit
from mari_components.connectors import connector_definition, connector_definitions
from mari_components.substrates import SourceRegistration
from mari_server.substrates.service import configured_substrate
from mari_server.persistence.postgres import substrate_references
from mari_server.persistence.postgres import knowledge as knowledge_store

router = APIRouter(prefix="/connectors")

# Reading the catalog is a listing of what this build supports — any signed-in
# member may see it. /validate and /connect both take a live credential and
# make the server talk to a third party with it, and /connect stores it on a
# source row: those are the admin operations the GraphQL side calls
# connectSource, so they carry the same guard here (AUTH-4). The router-level
# `dependencies=_authed` in app.py stays; this narrows the two that write.
_admin = [Depends(auth.require_admin)]

class ProviderIn(BaseModel):
    provider: str
    config: dict = {}


def _field_specs(definition) -> list[dict]:
    """Field SPECS only — never stored values."""
    return [{"key": field.key, "label": field.label, "secret": field.secret,
             "placeholder": field.placeholder, "help": field.help,
             "required": field.required} for field in definition.fields]


def _connected_map() -> dict[str, int]:
    """provider key → newest live source id."""
    substrate = configured_substrate()
    info = substrate.info()
    if info.provider != "native":
        rows = substrate_references.record_sources(
            access.require_current_access().project_id, info.provider,
            substrate.list_sources(),
        )
        aliases = {"google_drive": "gdrive"}
        return {aliases.get(str(row["kind"]), str(row["kind"])): int(row["id"]) for row in rows}
    out: dict[str, int] = {}
    for r in source_store.connector_sources():
        cfg = r["config"] if isinstance(r["config"], dict) else json.loads(r["config"] or "{}")
        if r["kind"] == "connector":
            out[connector_sync.provider_key_of(r["provider"], cfg)] = r["id"]
    return out


@router.get("/catalog")
def catalog() -> list[dict]:
    entries = {
        definition.key: {
            "key": definition.key,
            "name": definition.name,
            "blurb": definition.description,
            "fields": _field_specs(definition),
            "docsUrl": definition.documentation_url,
            "builtin": False,
        }
        for definition in connector_definitions()
    }
    connected = _connected_map()
    for key, item in entries.items():
        item["connected"] = key in connected
        item["sourceId"] = connected.get(key)
    return list(entries.values())


# ——— what a failed validate is allowed to say ———
#
# The user needs the real reason ("Bad credentials", "Repository not found",
# "Help Center is not enabled") — the console shows this string verbatim, and
# flattening it to a house apology would make the connect flow unusable. But
# connector modules quote the vendor's own response body into that message
# (`_vendor_error` in dropbox/gdrive/airtable/trello, jira's `errorMessages`),
# and the vendor is whatever host the caller named. So the message is passed
# through, scrubbed, never echoed raw (AUTH-11):
#
#   * control characters and newlines collapse to single spaces — no terminal
#     escapes, no log forging, no multi-line payload;
#   * anything that looks like markup is replaced wholesale, so an arbitrary
#     host's HTML/JS/XML never lands in a console string;
#   * 300-character cap — enough for a real vendor message, not a data channel.
#
# The SSRF guard is what stops the port scanner; this stops the reflection.

_ERR_CAP = 300
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_MARKUP_RE = re.compile(r"<\s*(?:!|/|\?|%|[a-zA-Z][\w:-]*(?:\s|/?>))")

log = logging.getLogger(__name__)


def _clean_error(text: str) -> str:
    """A vendor/connector message, safe to hand back to the caller."""
    text = _CONTROL_RE.sub(" ", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    if _MARKUP_RE.search(text):
        return ("The host answered with a web page, not an API response — check "
                "the URL points at the API and not at a login or proxy page.")
    return text[:_ERR_CAP] + "…" if len(text) > _ERR_CAP else text


def _error_for(provider: str, e: Exception) -> str:
    """Turn a raised exception into the string the user sees.

    A connector's own exception type carries a message we wrote and the vendor
    detail the user needs, so it is scrubbed and shown. Anything else is a bug
    in the connector, not something the user can fix: the type is named (so the
    report is actionable) and the full traceback goes to the server log."""
    authored = (type(e).__module__ or "").startswith(("connectors", "mari_components")) or isinstance(
        e, (ValueError, RuntimeError, ConnectionError, TimeoutError))
    if authored:
        return _clean_error(e) or f"{type(e).__name__} from the {provider} connector"
    log.exception("connector '%s' validate() crashed", provider)
    return (f"The {provider} connector crashed while checking the connection "
            f"({type(e).__name__}) — this is a bug; the details are in the server log.")


@router.post("/validate", dependencies=_admin)
def validate(body: ProviderIn) -> dict:
    try:
        connector_definition(body.provider)
    except KeyError:
        return {"ok": False, "error": f"Unknown connector provider '{body.provider}'"}
    try:
        err = component_connectors.validate_config(body.provider, body.config or {})
    except Exception as e:  # noqa: BLE001 — an honest error beats a 500
        err = _error_for(body.provider, e)
    return {"ok": err is None, "error": _clean_error(err) if err else ""}


def _qualifier(definition, config: dict) -> str:
    """Short instance label from the first identifying non-secret field."""
    secret = {field.key for field in definition.fields if field.secret}
    for k in definition.qualifier_fields:
        v = (config.get(k) or "").strip() if isinstance(config.get(k), str) else ""
        if v and k not in secret:
            v = v.replace("https://", "").replace("http://", "").rstrip("/")
            return v[:48]
    return ""


@router.post("/connect", dependencies=_admin)
def connect(body: ProviderIn) -> dict:
    check = validate(body)
    if not check["ok"]:
        return {"error": check["error"]}  # honest failure; no source row created
    definition = connector_definition(body.provider)
    key = definition.key

    qual = _qualifier(definition, body.config or {})
    provider_col = f"{key}:{qual}" if qual else key
    display = f"{definition.name} — {qual}" if qual else definition.name
    substrate = configured_substrate()
    info = substrate.info()
    if info.provider != "native":
        registration = _substrate_registration(key, display, body.config or {})
        existing_sources = substrate.list_sources()
        existing = next((source for source in existing_sources
                         if source.kind == registration.kind
                         and source.name.casefold() == registration.name.casefold()), None)
        if existing:
            rows = substrate_references.record_sources(
                access.require_current_access().project_id, info.provider,
                existing_sources,
            )
            row = next((value for value in rows if value["external_id"] == existing.source_id), None)
            if not row:
                return {"error": "The existing substrate source could not be resolved."}
            return {"sourceId": int(row["id"])}
        remote = substrate.create_source(registration)
        rows = substrate_references.record_sources(
            access.require_current_access().project_id, info.provider,
            substrate.list_sources(),
        )
        row = next((value for value in rows if value["external_id"] == remote.source_id), None)
        if not row:
            return {"error": "The substrate created the source but did not return it in its catalog."}
        substrate.run_source(remote.source_id, full=True)
        knowledge_store.create_task(
            title=f"Review newly connected source: {display}", assignee="", initials="",
            kind="approval", kind_label="Source review", due_date=None,
            subject=("source", str(row["id"]), display, "/sources"),
        )
        audit("connected source", display)
        return {"sourceId": int(row["id"])}
    cfg = dict(body.config or {})
    cfg.update({"provider_key": key, "cursor": "", "item_hashes": {},
                "last_sync_at": "", "last_error": ""})
    source_id = source_store.add_connector(provider_col, display, cfg)
    if source_id is None:
        return {"error": f"{display} is already connected"}
    audit("connected source", display)
    # every connected source gets a scheduled sync flow (Flows UI owns cadence)
    flowengine.ensure_sync_flow(source_id, display)
    ingest.start_sync(source_id)  # dispatches to connect_sync by kind
    return {"sourceId": source_id}


def _substrate_registration(key: str, display: str, cfg: dict) -> SourceRegistration:
    """Translate Mari's stable connector form into the selected substrate contract."""
    if key == "github":
        owner, repository = str(cfg["repo"]).split("/", 1)
        return SourceRegistration(
            display, "github",
            {"repo_owner": owner, "repositories": repository,
             "branch": str(cfg.get("branch") or "main"), "include_prs": True,
             "include_issues": True, "include_files": True},
            {"github_access_token": str(cfg["token"])}, 600, 86400,
        )
    if key == "slack":
        channels = [value.strip() for value in str(cfg.get("channels") or "").split(",") if value.strip()]
        return SourceRegistration(
            display, "slack",
            {"channels": channels or None, "exclude_channels": None,
             "include_bot_messages": False, "channel_regex_enabled": False,
             "exclude_channel_regex_enabled": False},
            {"slack_bot_token": str(cfg["bot_token"])}, 600, 86400,
        )
    if key == "confluence":
        return SourceRegistration(
            display, "confluence",
            {"wiki_base": str(cfg["site_url"]).rstrip("/"),
             "space": str(cfg.get("space_key") or "") or None,
             "is_cloud": True, "index_recursively": True, "include_attachments": True},
            {"confluence_username": str(cfg["email"]),
             "confluence_access_token": str(cfg["api_token"])}, 600, 86400,
        )
    if key == "gdrive":
        tokens = json.dumps({
            "token": str(cfg["access_token"]),
            "refresh_token": str(cfg.get("refresh_token") or ""),
            "client_id": str(cfg.get("client_id") or ""),
            "client_secret": str(cfg.get("client_secret") or ""),
            "token_uri": "https://oauth2.googleapis.com/token",
        })
        folder_id = str(cfg.get("folder_id") or "").strip()
        return SourceRegistration(
            display, "google_drive",
            {"include_shared_drives": False, "include_my_drives": not bool(folder_id),
             "shared_folder_urls": (f"https://drive.google.com/drive/folders/{folder_id}"
                                    if folder_id else "")},
            {"google_tokens": tokens, "google_primary_admin": ""}, 600, 86400,
        )
    raise ValueError(f"Connector {key!r} is not supported by the configured substrate")

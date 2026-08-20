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

from mari_server.api import auth
from mari_server.api import access
from mari_server.services import connector_sync
from mari_server.integrations import connector_provider as component_connectors
from mari_server.services import workflow_runtime as flowengine
from mari_server.services import sync as ingest
from mari_server.repositories.database import audit, exec_, q, q1
from mari_components.connectors import connector_definition, connector_definitions

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
    out: dict[str, int] = {}
    for r in q("""SELECT id, kind, provider, config FROM sources
                  WHERE project_id = %s AND kind = 'connector' ORDER BY id""",
               (access.require_current_access().project_id,)):
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
    project_id = access.require_current_access().project_id
    if q1("SELECT id FROM sources WHERE project_id = %s AND kind = 'connector' AND provider = %s", (project_id, provider_col)):
        return {"error": f"{display} is already connected"}

    cfg = dict(body.config or {})
    cfg.update({"provider_key": key, "cursor": "", "item_hashes": {},
                "last_sync_at": "", "last_error": ""})
    exec_("""INSERT INTO sources (project_id, provider, display_name, kind, status, stat_num, stat_unit,
                                  bars, config, docs_count, health)
             VALUES (%s, %s, %s, 'connector', 'active', '0', 'docs', '{}', %s, 0, 'Syncing')""",
          (project_id, provider_col, display, json.dumps(cfg)))
    source_id = q1("SELECT id FROM sources WHERE project_id = %s AND provider = %s", (project_id, provider_col))["id"]
    exec_("""INSERT INTO sync_events (provider, event, detail, at_label)
             VALUES (%s, %s, '', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
          (provider_col, f"connected: {display}"))
    audit("connected source", display)
    # every connected source gets a scheduled sync flow (Flows UI owns cadence)
    flowengine.ensure_sync_flow(source_id, display)
    ingest.start_sync(source_id)  # dispatches to connect_sync by kind
    return {"sourceId": source_id}

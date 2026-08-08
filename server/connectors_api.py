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

import auth
import connect_sync
import connectors
import flowengine
import github
import ingest
from db import audit, exec_, q, q1

router = APIRouter(prefix="/connectors")

# Reading the catalog is a listing of what this build supports — any signed-in
# member may see it. /validate and /connect both take a live credential and
# make the server talk to a third party with it, and /connect stores it on a
# source row: those are the admin operations the GraphQL side calls
# connectSource, so they carry the same guard here (AUTH-4). The router-level
# `dependencies=_authed` in app.py stays; this narrows the two that write.
_admin = [Depends(auth.require_admin)]

# Main-step order (contract): the rest appear under "Show all".
TOP8 = ["github", "slack", "upload", "website", "notion", "gdrive", "confluence", "jira"]

# Existing, fully-live connectors that are not provider modules: github has a
# dedicated repo-picker path (connectGithubRepo), upload posts to /onboard/upload.
BUILTIN = [
    {"key": "github", "name": "GitHub", "builtin": True,
     "blurb": "Markdown docs, issues, PRs and commit messages from your repos.",
     "fields": [
         {"key": "token", "label": "Fine-grained personal access token", "secret": True,
          "placeholder": "github_pat_…",
          "help": "1. Open “Where do I get these?” above. 2. Choose the resource owner and only the repositories Mari should read. 3. Under Repository permissions, grant read-only Contents, Issues, Pull requests, and Metadata. 4. Generate the token and paste it here."},
         {"key": "repo", "label": "Repository", "placeholder": "owner/repository",
          "help": "Enter one repository selected for the token, for example MariHQ/mari."},
         {"key": "paths", "label": "Paths filter (optional)", "placeholder": "docs/**",
          "required": False,
          "help": "Leave blank to ingest all supported files, or narrow the sync with a glob such as docs/**."},
     ], "docsUrl": "https://github.com/settings/personal-access-tokens/new"},
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
             "help": f.get("help", ""),
             # Older provider modules mark optional inputs in their label.
             # Preserve that contract while exposing a machine-readable flag
             # so clients never gate Test/Connect on an optional blank.
             "required": bool(f.get(
                 "required", "(optional)" not in str(f.get("label", "")).lower()
             ))} for f in provider.get("fields", [])]


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
    authored = (type(e).__module__ or "").startswith("connectors") or isinstance(
        e, (ValueError, RuntimeError, ConnectionError, TimeoutError))
    if authored:
        return _clean_error(e) or f"{type(e).__name__} from the {provider} connector"
    log.exception("connector '%s' validate() crashed", provider)
    return (f"The {provider} connector crashed while checking the connection "
            f"({type(e).__name__}) — this is a bug; the details are in the server log.")


@router.post("/validate", dependencies=_admin)
def validate(body: ProviderIn) -> dict:
    if body.provider == "github":
        cfg = body.config or {}
        token = str(cfg.get("token") or "").strip()
        repo = str(cfg.get("repo") or "").strip()
        if not token:
            return {"ok": False, "error": "Enter a GitHub personal access token."}
        if not repo or "/" not in repo:
            return {"ok": False, "error": "Name the repository as owner/repository."}
        state = github.push_token(token)
        try:
            github.default_branch(repo)
            return {"ok": True, "error": ""}
        except github.GithubError as e:
            return {"ok": False, "error": _clean_error(str(e))}
        finally:
            github.pop_token(state)
    entry = connectors.REGISTRY.get(body.provider)
    if not entry or not entry.get("provider"):
        connectors.REGISTRY.refresh()
        entry = connectors.REGISTRY.get(body.provider)
    if not entry or not entry.get("provider"):
        return {"ok": False, "error": f"Unknown connector provider '{body.provider}'"}
    try:
        err = entry["validate"](body.config or {})
    except Exception as e:  # noqa: BLE001 — an honest error beats a 500
        err = _error_for(body.provider, e)
    return {"ok": err is None, "error": _clean_error(err) if err else ""}


def _qualifier(provider: dict, config: dict) -> str:
    """Short instance label from the first identifying non-secret field."""
    secret = {f["key"] for f in provider.get("fields", []) if f.get("secret")}
    for k in QUALIFIER_KEYS:
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

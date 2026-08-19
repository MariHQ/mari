"""Airtable connector.

One Item per table in the configured base: table name plus up to 200
records rendered as a markdown list of their fields. Airtable has no
change feed on this surface, so there is no incremental cursor; the
hash_hint is a sha256 of the rendered body so unchanged tables are
skipped by the pipeline's hash check.

Standalone-importable; all raw HTTP goes through _http() (patchable in tests).
"""

import hashlib
import json
import urllib.parse

from . import _net
from ._protocol import ACLMetadata, PollResult, call_with_retry

API = "https://api.airtable.com/v0"

PROVIDER = {
    "key": "airtable",
    "name": "Airtable",
    "blurb": "Tables and records from one Airtable base.",
    "fields": [
        {
            "key": "pat",
            "label": "Personal access token",
            "secret": True,
            "placeholder": "pat…",
            "help": "https://airtable.com/create/tokens — scopes data.records:read and schema.bases:read, with access to your base.",
        },
        {
            "key": "base_id",
            "label": "Base ID",
            "secret": False,
            "placeholder": "appXXXXXXXXXXXXXX",
            "help": "From the base URL: airtable.com/appXXXXXXXXXXXXXX/…",
        },
    ],
    "docs_url": "https://airtable.com/developers/web/api/introduction",
}

_MAX_RECORDS = 200
_PAGE_SIZE = 100
_MAX_TABLES = 500


class AirtableError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def _http(method, url, headers=None, body=None, timeout=30):
    """All raw HTTP for this connector. Returns (status, bytes).

    Routed through the shared SSRF guard (AUTH-11): http/https only, no
    private/loopback/link-local target, every redirect hop re-checked, and the
    Authorization header dropped if a redirect crosses to another origin."""
    try:
        resp = _net.fetch(url, method=method, headers=headers or {}, data=body,
                          timeout=timeout)
        return resp.status, resp.body
    except _net.Blocked as e:
        raise ConnectionError(f"Airtable: refused to fetch {url} — {e}") from None
    except _net.NetworkError as e:
        raise ConnectionError(f"Airtable: network error: {e}") from None


def _headers(config):
    return {"Authorization": f"Bearer {config.get('pat', '')}"}


def _vendor_error(status, raw):
    try:
        err = json.loads(raw.decode("utf-8", "replace")).get("error")
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:
        msg = raw.decode("utf-8", "replace")[:300]
    return f"Airtable API error (HTTP {status}): {msg}"


def _get_json(config, url):
    def request():
        status, raw = _http("GET", url, headers=_headers(config))
        if status != 200:
            raise AirtableError(_vendor_error(status, raw), status)
        return json.loads(raw.decode("utf-8"))

    return call_with_retry(request)


def validate(config):
    if not (config.get("pat") or "").strip():
        return "Personal access token is required."
    base = (config.get("base_id") or "").strip()
    if not base:
        return "Base ID is required."
    def request():
        status, raw = _http(
            "GET", f"{API}/meta/bases/{urllib.parse.quote(base)}/tables",
            headers=_headers(config),
        )
        if status != 200:
            raise AirtableError(_vendor_error(status, raw), status)

    try:
        call_with_retry(request)
    except (AirtableError, ConnectionError) as error:
        return str(error)
    else:
        return None


def _fmt_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def _render_table(table, records):
    fields_order = [f.get("name") for f in table.get("fields", [])]
    lines = [f"# {table.get('name', 'Untitled table')}"]
    if table.get("description"):
        lines += ["", table["description"].strip()]
    for r in records:
        rf = r.get("fields", {})
        primary = None
        for name in fields_order:
            if name in rf:
                primary = _fmt_value(rf[name])
                break
        lines += ["", f"## {primary or r.get('id', '')}"]
        for name in fields_order:
            if name in rf:
                lines.append(f"- {name}: {_fmt_value(rf[name])}")
    return "\n".join(lines).rstrip() + "\n"


def _fetch_records(config, base, table_id):
    records, offset = [], None
    while len(records) < _MAX_RECORDS:
        params = {"pageSize": str(min(_PAGE_SIZE, _MAX_RECORDS - len(records)))}
        if offset:
            params["offset"] = offset
        url = f"{API}/{urllib.parse.quote(base)}/{urllib.parse.quote(table_id)}?{urllib.parse.urlencode(params)}"
        data = _get_json(config, url)
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    # An offset after reaching the local safety cap means this is not a full
    # table snapshot.  The caller must not reconcile missing rows as deletes.
    return records[:_MAX_RECORDS], not bool(offset)


def _fetch_tables(config, base):
    tables, offset = [], None
    while len(tables) < _MAX_TABLES:
        params = {"offset": offset} if offset else {}
        suffix = f"?{urllib.parse.urlencode(params)}" if params else ""
        data = _get_json(
            config,
            f"{API}/meta/bases/{urllib.parse.quote(base)}/tables{suffix}",
        )
        tables.extend(data.get("tables", []))
        offset = data.get("offset")
        if not offset:
            break
    return tables[:_MAX_TABLES], not bool(offset), offset


def list_items(config, cursor) -> PollResult:
    """No provider-native change feed: cursor unused, always returns None."""
    base = (config.get("base_id") or "").strip()
    tables, snapshot_complete, checkpoint = _fetch_tables(config, base)
    items = []
    for table in tables:
        tid = table["id"]
        records, table_complete = _fetch_records(config, base, tid)
        snapshot_complete = snapshot_complete and table_complete
        body = _render_table(table, records)
        updated = ""
        for r in records:
            t = r.get("createdTime") or ""
            if t > updated:
                updated = t
        items.append({
            "path": tid,
            "title": table.get("name", tid),
            "body": body,
            "updated_at": updated,
            "hash_hint": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "acl": ACLMetadata(visibility="connector_scope"),
        })
    return PollResult(items, None, snapshot_complete=snapshot_complete,
                      checkpoint=checkpoint)

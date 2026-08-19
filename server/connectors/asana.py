"""Asana connector.

One Item per project: project brief plus its task list (names + notes).
Incremental: tasks?modified_since=<cursor> detects changed projects; changed
projects get a full task refetch so the rendered body stays complete.

Standalone-importable; all raw HTTP goes through _http() (patchable in tests).
"""

import json
import urllib.parse

from . import _net
from ._protocol import ACLMetadata, PollResult, call_with_retry

API = "https://app.asana.com/api/1.0"

PROVIDER = {
    "key": "asana",
    "name": "Asana",
    "blurb": "Projects and tasks your team plans work in.",
    "fields": [
        {
            "key": "pat",
            "label": "Personal access token",
            "secret": True,
            "placeholder": "1/1234…",
            "help": "Asana → Settings → Apps → Developer apps → Personal access tokens.",
        },
        {
            "key": "workspace",
            "label": "Workspace GID (optional)",
            "secret": False,
            "placeholder": "1200000000000000",
            "help": "Limit to one workspace. Blank = your default workspace.",
        },
    ],
    "docs_url": "https://developers.asana.com/docs/personal-access-token",
}

_TASK_FIELDS = "name,notes,completed,modified_at"
_PAGE_LIMIT = 100
_MAX_PAGES = 100


class AsanaError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class _PagedList(list):
    def __init__(self, values=(), *, complete=True, checkpoint=None):
        super().__init__(values)
        self.complete = complete
        self.checkpoint = checkpoint


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
        raise ConnectionError(f"Asana: refused to fetch {url} — {e}") from None
    except _net.NetworkError as e:
        raise ConnectionError(f"Asana: network error: {e}") from None


def _headers(config):
    return {
        "Authorization": f"Bearer {config.get('pat', '')}",
        "Accept": "application/json",
    }


def _vendor_error(status, raw):
    try:
        errs = json.loads(raw.decode("utf-8", "replace")).get("errors", [])
        msg = "; ".join(e.get("message", "") for e in errs) or raw.decode("utf-8", "replace")[:300]
    except Exception:
        msg = raw.decode("utf-8", "replace")[:300]
    return f"Asana API error (HTTP {status}): {msg}"


def _get_paginated(config, path, params):
    """Follow Asana offset pagination; returns list of data dicts."""
    out, offset = [], None
    for _ in range(_MAX_PAGES):
        p = dict(params, limit=str(_PAGE_LIMIT))
        if offset:
            p["offset"] = offset
        url = f"{API}{path}?{urllib.parse.urlencode(p)}"
        def request():
            status, raw = _http("GET", url, headers=_headers(config))
            if status != 200:
                raise AsanaError(_vendor_error(status, raw), status)
            return json.loads(raw.decode("utf-8"))

        data = call_with_retry(request)
        out.extend(data.get("data", []))
        offset = (data.get("next_page") or {}).get("offset")
        if not offset:
            return _PagedList(out, complete=True)
    return _PagedList(out, complete=False, checkpoint=offset)


def validate(config):
    if not (config.get("pat") or "").strip():
        return "Personal access token is required."
    def request():
        status, raw = _http("GET", f"{API}/users/me", headers=_headers(config))
        if status != 200:
            raise AsanaError(_vendor_error(status, raw), status)

    try:
        call_with_retry(request)
    except (AsanaError, ConnectionError) as error:
        return str(error)
    else:
        return None


def _render_project(project, tasks):
    lines = [f"# {project.get('name', 'Untitled project')}"]
    if project.get("notes"):
        lines += ["", project["notes"].strip()]
    lines.append("")
    for t in tasks:
        mark = "x" if t.get("completed") else " "
        lines.append(f"- [{mark}] {t.get('name', '')}")
        notes = (t.get("notes") or "").strip()
        if notes:
            lines.extend(f"  {ln}" for ln in notes.splitlines())
    return "\n".join(lines).rstrip() + "\n"


def list_items(config, cursor) -> PollResult:
    """cursor = ISO timestamp of the newest task modified_at seen so far."""
    proj_params = {"opt_fields": "name,notes,modified_at"}
    workspace = (config.get("workspace") or "").strip()
    if workspace:
        proj_params["workspace"] = workspace
    projects = _get_paginated(config, "/projects", proj_params)

    items, newest = [], cursor
    snapshot_complete = getattr(projects, "complete", True)
    checkpoint = getattr(projects, "checkpoint", None)
    for p in projects:
        gid = p["gid"]
        task_params = {"project": gid, "opt_fields": _TASK_FIELDS}
        if cursor:
            probe = _get_paginated(config, "/tasks", dict(task_params, modified_since=cursor))
            snapshot_complete = snapshot_complete and getattr(probe, "complete", True)
            checkpoint = checkpoint or getattr(probe, "checkpoint", None)
            if not probe and (p.get("modified_at") or "") <= cursor:
                continue  # unchanged project on incremental sync
        tasks = _get_paginated(config, "/tasks", task_params)
        snapshot_complete = snapshot_complete and getattr(tasks, "complete", True)
        checkpoint = checkpoint or getattr(tasks, "checkpoint", None)

        latest = p.get("modified_at") or ""
        for t in tasks:
            m = t.get("modified_at") or ""
            if m > latest:
                latest = m
        if newest is None or latest > newest:
            newest = latest

        items.append({
            "path": gid,
            "title": p.get("name", gid),
            "body": _render_project(p, tasks),
            "updated_at": latest,
            "hash_hint": latest or None,
            "acl": ACLMetadata(visibility="connector_scope"),
        })
    return PollResult(items, newest if snapshot_complete else cursor,
                      snapshot_complete=snapshot_complete, checkpoint=checkpoint)

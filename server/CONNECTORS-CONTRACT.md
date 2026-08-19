# Connectors contract

This is the frozen contract every source connector implements. The generic sync
worker (`connect_sync.py`), the REST surface (`connectors_api.py`), and the
provider registry (`connectors/__init__.py`) all rely on it. Changing the shapes
here is a deliberate, separately discussed step, because every connector depends
on them at once.

If you are adding a connector, this is the whole specification. Copy an existing
module of similar shape (`connectors/trello.py` is a compact two-field example)
and make it satisfy what follows.

## How a connector is discovered

A connector is one Python module in `server/connectors/`. The registry globs the
directory on first access and imports every `*.py` whose name does not start with
`_` (underscore-prefixed files are helpers and are skipped). There is no
hardcoded list and nothing to register by hand: drop the module in and it
appears.

A module qualifies only if it exposes all three of the names below. A module that
fails to import, or is missing any of them, is recorded as unavailable with a
warning rather than breaking the registry.

```python
PROVIDER   # dict, described below
def validate(config: dict) -> str | None: ...
def list_items(config: dict, cursor: str | None) -> PollResult: ...
```

## `PROVIDER`

Describes the connector to the catalog and the connect form. Field values are
never stored here, only the field SPECS.

```python
PROVIDER = {
    "key":   "trello",          # unique, lowercase; the registry key and the
                                # prefix in source_path and document ids
    "name":  "Trello",          # display name, also used as the sync author
    "blurb": "Boards, lists, and cards your team tracks work on.",
    "fields": [ ... ],          # the connect form, described below
    "docs_url": "https://…",    # optional: "Where do I get these?" link target
}
```

Each entry in `fields` is one input on the connect form:

```python
{
    "key":         "api_key",       # the config dict key this input writes
    "label":       "API key",       # form label
    "secret":      True,            # optional; masks the value everywhere it
                                    # could be returned. Default False.
    "placeholder": "32-char hex key",
    "help":        "Where to find this value, step by step.",
    "required":    False,           # optional; default True
}
```

Any field with `"secret": True` is masked (`••••••`) in every API response.
Credentials live only in `sources.config` and are never returned. Give secret
fields honest `help` text: the person connecting is usually looking at a vendor
settings page they have never seen before.

## `validate(config) -> str | None`

The pre-connect "Test connection" check. It receives the same `config` dict the
form produced and must actually reach the third party with the supplied
credentials, cheaply.

- Return `None` when the credentials work.
- Return a short human-readable string when they do not. That string is shown to
  the person connecting, so make it about what they can fix ("API key is
  required.", the vendor's error text, not a stack trace).

Check for missing required fields first, then make one cheap authenticated call
(fetch the current user, list one page). Do not raise for an expected auth
failure: return the message.

## `list_items(config, cursor) -> PollResult`

Lists the documents to ingest. This is where incremental sync lives.

`cursor` is whatever string you returned as `new_cursor` on the previous run, or
`None` on the first run and on a full resync. Use it to skip work the vendor tells
you is unchanged (a high-water timestamp, an opaque delta token, a page marker).

Return `connectors._protocol.PollResult`. The worker continues to accept the old
`(items, new_cursor)` tuple while existing connectors migrate, and `PollResult`
itself can still be unpacked as two values by older tests/callers.

```python
PollResult(
    items=[...],                 # documents in the shape below
    cursor="opaque-or-time",     # cursor to persist after a complete poll
    snapshot_complete=True,      # False when a page/safety cap was reached
    tombstones=["stable/path"],  # explicit deletes from an incremental feed
    checkpoint=None,             # optional provider checkpoint for observability
)
```

When `snapshot_complete` is false the worker ingests returned changes but holds
the previous cursor and never infers deletion from absence. This prevents a
provider safety cap or interrupted listing from deleting valid documents or
skipping the unvisited tail. Native delta feeds should return deleted paths as
`tombstones`; these are authoritative even on an incremental run.

Each item:

```python
{
    "path":       "board-id-123",     # required; unique and STABLE per document
                                      # within this connector. It is the identity
                                      # used to detect renames, updates, deletes.
    "title":      "Q3 Roadmap",       # falls back to path if blank
    "body":       "# Q3 Roadmap\n…",  # the document text (markdown). Empty body
                                      # is recorded but never chunked or embedded.
    "updated_at": "2026-08-01T…Z",    # optional; for display
    "hash_hint":  "2026-08-01T…Z",    # optional; see below
    "acl": ACLMetadata(               # optional; omitted never means public
        visibility="connector_scope",
        principals=("group:engineering",),
    ),
}
```

### How change detection works

The worker decides whether to re-embed a document by comparing a per-item hash
against the last run:

- If you provide `hash_hint`, the worker uses it verbatim as the item's hash. Set
  it to something that changes if and only if the body changed (the vendor's
  `updated_at`, a version number, an etag). This lets the worker skip re-fetching
  and re-embedding unchanged items cheaply.
- If you omit `hash_hint`, the worker hashes `title + body` itself. Correct, but
  it means you already fetched the full body to find out nothing changed.

Prefer `hash_hint` whenever the vendor gives you a cheap change signal. Either
way, unchanged content is never re-embedded. Keeping ingestion incremental is a
project-wide rule, not a per-connector nicety.

### Deletes and incomplete snapshots

On a full resync (`cursor` arrives as `None`), return the connector's complete
current set. The worker deletes any previously ingested document whose `path` is
no longer present only if `snapshot_complete=True`. If the provider's page cap
is reached, return `snapshot_complete=False`. On an incremental run, return
explicit deletion markers in `tombstones`; absence is never deletion.

## Errors and retries

The worker centrally classifies connector exceptions as auth, rate-limit,
transient, or permanent failures. It retries only rate-limit and transient
failures, with a bounded delay. Exceptions should expose an integer `status`
when the provider supplies an HTTP status. A connector may raise
`ConnectorCallError` when it needs to state the class or `retry_after` directly.

## Networking: use the shared guard, always

Every outbound HTTP call must go through the shared SSRF guard, never
`urllib.request.urlopen` directly:

```python
from . import _net

resp = _net.fetch(url, method="GET", headers={...}, timeout=30)
# resp.status, resp.body
```

`_net.fetch` enforces http/https only, refuses private, loopback, and link-local
targets, re-checks every redirect hop, and drops the `Authorization` header if a
redirect crosses to another origin. It raises `_net.Blocked` for a refused target
and `_net.NetworkError` for a transport failure. Catch those and re-raise as a
`ConnectionError` or `RuntimeError` with a message safe to show the user. Bypassing
`_net` reintroduces the SSRF hole the guard exists to close.

## Config keys the worker owns

The worker writes bookkeeping into the same `config` dict. Do not use these keys
for your own fields: `provider_key`, `cursor`, `item_hashes`, `last_sync_at`,
`last_error`. Everything else in `config` is yours (the form field values).

`sources.provider` is either your `key` or `key:qualifier` (a qualifier lets one
provider back several distinct sources). `source_path` and document ids are
prefixed with the bare `key`.

## Checklist for a new connector

- [ ] `PROVIDER` with a unique `key`, a `name`, a `blurb`, and `fields` with
      honest `help` text. Secret fields marked `"secret": True`.
- [ ] `validate` makes one cheap authenticated call and returns `None` or a
      user-facing message.
- [ ] `list_items` returns `PollResult` (legacy tuples remain accepted) with stable `path`s, uses
      `cursor` to skip unchanged work, and sets `hash_hint` when the vendor offers
      a cheap change signal.
- [ ] Page/safety caps set `snapshot_complete=False`; native deleted entries
      become `tombstones`.
- [ ] ACL metadata is supplied when the provider exposes it. Missing ACL data is
      connector-scoped, never implicitly public.
- [ ] All HTTP goes through `_net.fetch`.
- [ ] The module is importable on its own (`python -c "import connectors.yours"`),
      so it does not import server internals at module top level beyond `_net`.
- [ ] No new dependency on the reserved worker config keys.

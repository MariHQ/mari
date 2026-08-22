"""Postgres-backed connector ingestion runtime (CONNECTORS-CONTRACT.md).

One worker for every kind='connector' source: validate() → list_items(config,
cursor) → upsert documents (source_path = f"{provider}/{path}") → chunk +
content-hash + embed via the ingest helpers → delete vanished items on a full
resync → persist cursor + per-item hash map in sources.config → truthful
sync_events. Progress registers in ingest's in-memory status registry, so
syncStatus / syncSource / resyncSource behave identically to github sources
(ingest.start_sync/run_sync dispatch here by sources.kind).

Concurrency: shares ingest.LOCK/_RUNNING — one sync per source at a time,
across both engines. The _RUNNING slot is acquired by ingest.start_sync/
run_sync and released by ingest.run_guarded; this worker never touches it.
"""

from __future__ import annotations

import datetime as dt
import json
import time

from mari_server.persistence.postgres import document_index
from mari_server.identity import context as access
from mari_server.persistence.postgres import lineage as links
from mari_components import IncompleteSnapshot, SyncMode
from mari_components.sync import ManifestEntry, SyncState
from mari_components.connectors import CONNECTOR_CATALOG, call_with_retry, connector_definition
from mari_components.sync.ingestion import AppliedPage, consume_connector_pages
from mari_server.providers import connectors as connector_provider

# internal config keys the worker owns (never provider credential fields)
INTERNAL_KEYS = ("provider_key", "cursor", "item_hashes", "last_sync_at", "last_error",
                 "full_snapshot_pending", "full_snapshot_seen_paths")

# secret-ish config keys masked even when a provider module is unavailable
FALLBACK_SECRET_KEYS = {"token", "api_token", "api_key", "apikey", "secret",
                        "password", "access_token", "service_account_json", "bot_token"}

MASK = "••••••"


def provider_key_of(provider: str, cfg: dict) -> str:
    """sources.provider is `key` or `key:qualifier`; config.provider_key wins."""
    return (cfg.get("provider_key") or provider.split(":", 1)[0]).strip()


def secret_fields(key: str) -> set[str]:
    definition = CONNECTOR_CATALOG.get(key)
    return {field.key for field in definition.fields if field.secret} if definition else set()


def masked_config(provider: str, cfg: dict) -> dict:
    """Config safe to return from any API: secret values masked, bulky
    internal hash maps dropped. Used by sourcePulse for connector sources."""
    key = provider_key_of(provider, cfg)
    secrets = secret_fields(key) | FALLBACK_SECRET_KEYS
    out = {}
    for k, v in cfg.items():
        if k in ("item_hashes", "shas", "full_snapshot_seen_paths"):
            continue
        out[k] = MASK if (k in secrets and v) else v
    return out


def _event(conn, provider: str, event: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO sync_events (provider, event, detail, at_label)
           VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
        (provider, event, detail))


def _checkpoint(conn, provider: str, item: str, stage: str, done: int, total: int,
                cursor: str, status: str, started: float) -> None:
    conn.execute(
        """INSERT INTO ingest_checkpoints (provider, item, stage, progress, total, cursor_id,
                                           duration, status, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (provider, item) DO UPDATE SET stage = EXCLUDED.stage,
             progress = EXCLUDED.progress, total = EXCLUDED.total, cursor_id = EXCLUDED.cursor_id,
             duration = EXCLUDED.duration, status = EXCLUDED.status, updated_at = now()""",
        (provider, item, stage, done, max(total, 1), (cursor or "")[:64],
         time.strftime("%H:%M:%S", time.gmtime(time.time() - started)), status))


def document_author(document) -> str:
    """The real person behind one connector document, when the connector's
    page metadata exposes one (Confluence's version.by/history.createdBy,
    Jira's assignee/reporter, ...) — never the connector's own display name.
    Lineage and the Knowledge inspector show no owner rather than "Confluence"
    or "Jira" for a document nothing maps a person to."""
    return str((document.metadata or {}).get("author") or "").strip()


def deletion_ids(rows: list[dict], provider_key: str, seen_paths: set[str],
                 tombstones: set[str], *, full: bool,
                 snapshot_complete: bool) -> list[int]:
    """Select safe deletes without coupling connector semantics to the DB.

    Explicit tombstones are authoritative on incremental polls. Missing paths
    are authoritative only when a full poll declares its snapshot complete.
    """
    gone: list[int] = []
    for row in rows:
        source_path = row["source_path"] or ""
        relative = (source_path[len(provider_key) + 1:]
                    if source_path.startswith(f"{provider_key}/") else None)
        if relative in tombstones or (
                full and snapshot_complete and
                (relative is None or relative not in seen_paths)):
            gone.append(row["id"])
    return gone


def sync_source(source_id: int, full: bool, *, update_status, fire_document_triggers,
                invalidate_search) -> dict:
    """Run one connector sync. Returns honest stats (plus 'error' on failure) —
    the same shape flowengine's sync_source step reads from ingest.run_sync."""
    started = time.time()
    stats = {"files_changed": 0, "files_deleted": 0, "items_changed": 0,
             "chunks": 0, "embedded": 0, "skipped": 0}
    added_doc_ids: list[int] = []
    changed_doc_ids: list[int] = []
    provider_col = f"source #{source_id}"
    cfg: dict = {}

    with document_index.connection() as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = %s AND project_id = %s",
                           (source_id, access.require_current_access().project_id)).fetchone()
    if not src or src.get("kind") != "connector":
        # ingest.run_guarded releases the _RUNNING slot for every exit path
        update_status(source_id, state="error", phase="", error="not a connector source")
        return {**stats, "error": "not a connector source"}

    provider_col = src["provider"]
    display = src["display_name"]
    cfg = src["config"] if isinstance(src["config"], dict) else json.loads(src["config"] or "{}")
    key = provider_key_of(provider_col, cfg)
    sync_start_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        definition = connector_definition(key)

        max_tokens, overlap = document_index.chunk_settings()
        stored_hashes: dict = dict(cfg.get("item_hashes") or {})
        stored_cursor = cfg.get("cursor") or None
        stored_checkpoint = cfg.get("checkpoint") or None
        hashes = dict(stored_hashes)
        cursor = stored_cursor
        authoritative_full = full or bool(cfg.get("full_snapshot_pending"))
        snapshot_seen_paths = (set() if full else set(cfg.get("full_snapshot_seen_paths") or []))

        if full:
            # A rebuild is prepared in memory and becomes authoritative only
            # after a complete listing succeeds. Clearing chunks/cursors first
            # made a transient provider failure destroy the working index.
            cursor = None

        # —— validate (cheap, honest) ——
        update_status(source_id, state="running", phase="listing", done=0, total=0, error="")
        def validate_once() -> None:
            result = definition.validate(cfg, http=connector_provider.http_transport)
            if not result.ok:
                raise ValueError(result.message)
        call_with_retry(validate_once, sleep=time.sleep)

        # —— poll and apply one page at a time ——
        def provider_pages():
            yield from connector_provider.poll_pages(
                key, cfg, cursor, stored_checkpoint, full=authoritative_full)

        done = 0
        total = 0
        initials = (key[:2] or "??").upper()
        author = definition.name

        def apply_page(plan, _page_number):
            nonlocal done, total
            page_total = len(plan.upserts) + len(plan.unchanged) + len(plan.deletes)
            total += page_total
            inserted_ids: list[int] = []
            updated_ids: list[int] = []
            page_chunks = page_embeddings = 0
            documents_to_embed: list[tuple[int, str, str]] = []
            with document_index.connection() as conn:
                projection_rows: list[dict] = []
                for document in plan.upserts:
                    path = document.external_id
                    title = document.title.strip() or path
                    body = document.body
                    fingerprint = plan.state.manifest[path].fingerprint
                    done += 1
                    update_status(source_id, phase="chunking", done=done, total=total)
                    principals = tuple(
                        f"{principal.kind}:{principal.identifier}"
                        for principal in document.acl.principals
                    )
                    projection_rows.append({
                        "source_id": source_id,
                        "external_id": f"{key}:{source_id}:{path}",
                        "title": title, "body": body, "source_path": f"{key}/{path}",
                        "content_hash": fingerprint, "author": document_author(document), "source": key,
                        "initials": initials, "acl_visibility": document.acl.visibility,
                        "acl_principals": principals, "source_updated_at": document.updated_at,
                    })

                projected = document_index.upsert_documents(conn, projection_rows)
                for document, (doc_id, inserted) in zip(plan.upserts, projected, strict=True):
                    title = document.title.strip() or document.external_id
                    body = document.body
                    (inserted_ids if inserted else updated_ids).append(doc_id)
                    update_status(source_id, phase="embedding")
                    if body.strip():
                        documents_to_embed.append((doc_id, title, body))
                    else:
                        conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                        conn.execute("UPDATE documents SET embedding = NULL WHERE id = %s", (doc_id,))
                        chunks, embedded = 0, 0
                        page_chunks += chunks
                        page_embeddings += embedded

                if documents_to_embed:
                    page_chunks, page_embeddings = document_index.sync_chunks_many(
                        conn, documents_to_embed, max_tokens, overlap,
                    )

                done += len(plan.unchanged) + len(plan.deletes)
                tombstones = {item.external_id for item in plan.deletes}
                rows = conn.execute(
                    "SELECT id, source_path FROM documents WHERE source_id = %s", (source_id,),
                ).fetchall()
                gone = deletion_ids(rows, key, set(), tombstones, full=False,
                                    snapshot_complete=False)
                if gone:
                    document_index.delete_documents(conn, gone)

                # Document mutations and this replay checkpoint are one
                # transaction. A crash cannot advance beyond committed data.
                durable_cursor = plan.state.checkpoint or plan.state.cursor or ""
                hashes = {
                    path: manifest.fingerprint for path, manifest in plan.state.manifest.items()
                }
                cfg.update({
                    "provider_key": key, "cursor": durable_cursor, "item_hashes": hashes,
                    "checkpoint": plan.state.checkpoint or "",
                    "last_sync_at": sync_start_iso, "last_error": "",
                    "full_snapshot_pending": bool(authoritative_full and not plan.snapshot_complete),
                    "full_snapshot_seen_paths": (
                        sorted(plan.state.full_seen)
                        if authoritative_full and not plan.snapshot_complete else []
                    ),
                })
                doc_count = conn.execute(
                    "SELECT count(*) AS n FROM documents WHERE source_id = %s", (source_id,),
                ).fetchone()["n"]
                conn.execute(
                    """UPDATE sources SET config = %s, last_sync_at = now(), docs_count = %s,
                         stat_num = %s, stat_unit = 'docs', health = 'Healthy', status = 'active'
                         WHERE id = %s""",
                    (json.dumps(cfg), doc_count, str(doc_count), source_id),
                )
                _checkpoint(conn, provider_col, display, "embedded", done, total,
                            durable_cursor, "running", started)
                conn.commit()
            update_status(source_id, done=done, total=total)
            return AppliedPage(
                tuple(inserted_ids), tuple(updated_ids), len(gone),
                page_chunks, page_embeddings,
            )

        try:
            report = consume_connector_pages(
                provider_pages(),
                SyncState(
                    cursor=stored_cursor,
                    manifest={path: ManifestEntry(str(value)) for path, value in stored_hashes.items()},
                    full_seen=frozenset(snapshot_seen_paths),
                ),
                SyncMode.FULL if authoritative_full else SyncMode.INCREMENTAL,
                apply_page=apply_page,
            )
        except IncompleteSnapshot:
            if full:
                raise
            return sync_source(
                source_id, True, update_status=update_status,
                fire_document_triggers=fire_document_triggers,
                invalidate_search=invalidate_search,
            )

        added_doc_ids.extend(report.inserted_ids)
        changed_doc_ids.extend(report.updated_ids)
        reconciled_deletes = 0
        if authoritative_full and report.snapshot_complete:
            # The connector manifest is the durable fast path, but it is not
            # allowed to be the only deletion boundary. Older releases and
            # changed connector profiles can leave source rows that no longer
            # have manifest entries. A complete full snapshot is authoritative
            # over every document owned by this source, so reconcile the
            # projection itself against the terminal manifest as well.
            with document_index.connection() as conn:
                rows = conn.execute(
                    "SELECT id, source_path FROM documents WHERE source_id = %s", (source_id,),
                ).fetchall()
                gone = deletion_ids(
                    rows, key, set(report.state.manifest), set(),
                    full=True, snapshot_complete=True,
                )
                if gone:
                    document_index.delete_documents(conn, gone)
                    reconciled_deletes = len(gone)
                    doc_count = conn.execute(
                        "SELECT count(*) AS n FROM documents WHERE source_id = %s", (source_id,),
                    ).fetchone()["n"]
                    conn.execute(
                        "UPDATE sources SET docs_count = %s, stat_num = %s WHERE id = %s",
                        (doc_count, str(doc_count), source_id),
                    )
                    conn.commit()
        removed = report.deleted + reconciled_deletes
        stats.update({
            "items_changed": report.changed, "files_deleted": removed,
            "chunks": report.chunks, "embedded": report.embeddings,
            "skipped": report.unchanged,
        })
        if report.changed or removed:
            invalidate_search(access.require_current_access().project_id)
        durable_cursor = report.state.checkpoint or report.state.cursor or ""
        detail = (f"{report.changed} items changed · {removed} removed · "
                  f"{report.chunks} chunks · {report.embeddings} embedded · "
                  f"{report.unchanged} unchanged (hash skip)" +
                  (" · snapshot incomplete (cursor held; absence not reconciled)"
                   if not report.snapshot_complete else ""))
        with document_index.connection() as conn:
            _event(conn, provider_col, f"sync: {display}", detail)
            conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                         (f"{author} sync", f"synced {report.changed} items", display))
            _checkpoint(conn, provider_col, display, "indexed", done, total,
                        durable_cursor, "done", started)
            conn.commit()

        # —— link extraction + document triggers: best-effort, like github ——
        touched = added_doc_ids + changed_doc_ids
        try:
            if touched:
                counts = links.extract(source_id, touched)
                with document_index.connection() as conn:
                    _event(conn, provider_col, f"links: {display}",
                           f"{counts['references']} references · {counts['links_to']} links_to · "
                           f"{counts['similar']} similar (edges created, {len(touched)} docs scanned)")
                    conn.commit()
        except Exception as le:  # noqa: BLE001 — link extraction is best-effort
            with document_index.connection() as conn:
                _event(conn, provider_col, f"links error: {display}", str(le)[:300])
                conn.commit()
        try:
            fired = (fire_document_triggers(added_doc_ids, "document_added") +
                     fire_document_triggers(changed_doc_ids, "document_changed"))
            if fired:
                with document_index.connection() as conn:
                    _event(conn, provider_col, f"triggers: {display}",
                           f"{len(fired)} flow run(s) auto-started "
                           f"({len(added_doc_ids)} added, {len(changed_doc_ids)} changed docs)")
                    conn.commit()
        except Exception as te:  # noqa: BLE001 — triggers are best-effort
            with document_index.connection() as conn:
                _event(conn, provider_col, f"trigger error: {display}", str(te)[:300])
                conn.commit()

        update_status(source_id, state="idle", phase="done", done=done, total=total, error="")
        # flow step reads files_changed too — connectors count everything as items
        return {**stats, "files_changed": 0}
    except Exception as e:  # noqa: BLE001 — a sync must always land in a truthful state
        msg = str(e)[:300]
        update_status(source_id, state="error", phase="", error=msg)
        try:
            with document_index.connection() as conn:
                cfg["last_error"] = msg
                conn.execute("UPDATE sources SET config = %s, health = 'Error' WHERE id = %s",
                             (json.dumps(cfg), source_id))
                _event(conn, provider_col, f"sync failed: {provider_col}", msg)
                _checkpoint(conn, provider_col, provider_col, "fetched", 0, 1, "", "paused", started)
                conn.commit()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
        return {**stats, "error": msg}

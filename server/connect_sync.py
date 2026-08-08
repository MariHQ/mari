"""Mari — generic connector sync worker (CONNECTORS-CONTRACT.md).

One worker for every kind='connector' source: validate() → list_items(config,
cursor) → upsert documents (source_path = f"{provider}/{path}") → chunk +
content-hash + embed via the ingest helpers → delete vanished items on a full
resync → persist cursor + per-item hash map in sources.config → truthful
sync_events. Progress registers in ingest's in-memory status registry, so
syncStatus / syncSource / resyncSource behave identically to github sources
(ingest.start_sync/run_sync dispatch here by sources.kind).

Concurrency: shares ingest._LOCK/_RUNNING — one sync per source at a time,
across both engines. The _RUNNING slot is acquired by ingest.start_sync/
run_sync and released by ingest._run_guarded; this worker never touches it.
"""

from __future__ import annotations

import datetime as dt
import json
import time

import connectors
import flowengine
import ingest
import links

# internal config keys the worker owns (never provider credential fields)
INTERNAL_KEYS = ("provider_key", "cursor", "item_hashes", "last_sync_at", "last_error")

# secret-ish config keys masked even when a provider module is unavailable
FALLBACK_SECRET_KEYS = {"token", "api_token", "api_key", "apikey", "secret",
                        "password", "access_token", "service_account_json", "bot_token"}

MASK = "••••••"


def provider_key_of(provider: str, cfg: dict) -> str:
    """sources.provider is `key` or `key:qualifier`; config.provider_key wins."""
    return (cfg.get("provider_key") or provider.split(":", 1)[0]).strip()


def secret_fields(key: str) -> set[str]:
    entry = connectors.REGISTRY.get(key)
    if entry and entry.get("provider"):
        return {f["key"] for f in entry["provider"].get("fields", []) if f.get("secret")}
    return set()


def masked_config(provider: str, cfg: dict) -> dict:
    """Config safe to return from any API: secret values masked, bulky
    internal hash maps dropped. Used by sourcePulse for connector sources."""
    key = provider_key_of(provider, cfg)
    secrets = secret_fields(key) | FALLBACK_SECRET_KEYS
    out = {}
    for k, v in cfg.items():
        if k in ("item_hashes", "shas"):
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


def _sync_worker(source_id: int, full: bool) -> dict:
    """Run one connector sync. Returns honest stats (plus 'error' on failure) —
    the same shape flowengine's sync_source step reads from ingest.run_sync."""
    started = time.time()
    stats = {"files_changed": 0, "files_deleted": 0, "items_changed": 0,
             "chunks": 0, "embedded": 0, "skipped": 0}
    added_doc_ids: list[int] = []
    changed_doc_ids: list[int] = []
    provider_col = f"source #{source_id}"
    cfg: dict = {}

    with ingest._conn() as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()
    if not src or src.get("kind") != "connector":
        # ingest._run_guarded releases the _RUNNING slot for every exit path
        ingest._set(source_id, state="error", phase="", error="not a connector source")
        return {**stats, "error": "not a connector source"}

    provider_col = src["provider"]
    display = src["display_name"]
    cfg = src["config"] if isinstance(src["config"], dict) else json.loads(src["config"] or "{}")
    key = provider_key_of(provider_col, cfg)
    sync_start_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        entry = connectors.REGISTRY.get(key)
        if not entry or not entry.get("provider"):
            raise RuntimeError((entry or {}).get("error") or f"unknown connector provider '{key}'")

        max_tokens, overlap = ingest._chunk_settings()
        hashes: dict = dict(cfg.get("item_hashes") or {})
        cursor = cfg.get("cursor") or None

        if full:
            cursor, hashes = None, {}
            cfg["cursor"], cfg["item_hashes"] = "", {}
            with ingest._conn() as conn:
                conn.execute("""DELETE FROM chunks WHERE document_id IN
                                (SELECT id FROM documents WHERE source_id = %s)""", (source_id,))
                conn.execute("UPDATE documents SET content_hash = '' WHERE source_id = %s", (source_id,))
                # persist the cleared cursor so an interrupted rebuild resumes full
                conn.execute("UPDATE sources SET config = %s WHERE id = %s", (json.dumps(cfg), source_id))
                conn.commit()

        # —— validate (cheap, honest) ——
        ingest._set(source_id, state="running", phase="listing", done=0, total=0, error="")
        verr = entry["validate"](cfg)
        if verr:
            raise RuntimeError(str(verr))

        # —— list ——
        items, new_cursor = entry["list_items"](cfg, cursor)
        total = len(items)
        ingest._set(source_id, total=total)
        with ingest._conn() as conn:
            _checkpoint(conn, provider_col, display, "fetched", 0, total,
                        new_cursor or "", "running", started)
            conn.commit()

        done = 0
        seen_paths: set[str] = set()
        initials = (key[:2] or "??").upper()
        author = entry["provider"].get("name", key)

        with ingest._conn() as conn:
            for item in items:
                path = str(item.get("path") or "")
                if not path:
                    done += 1
                    continue
                seen_paths.add(path)
                title = (item.get("title") or path).strip() or path
                body = item.get("body") or ""
                hint = item.get("hash_hint")
                h = str(hint) if hint else ingest._sha(f"{title}\n\n{body}")
                done += 1
                if not full and hashes.get(path) == h:
                    stats["skipped"] += 1  # hash_hint / body hash unchanged — no re-embed
                    ingest._set(source_id, phase="fetching", done=done)
                    continue
                if not body.strip():
                    hashes[path] = h
                    ingest._set(source_id, done=done)
                    continue
                ingest._set(source_id, phase="chunking", done=done)
                doc_id, inserted = ingest._upsert_document(
                    conn, source_id, f"{key}:{source_id}:{path}", title, body,
                    f"{key}/{path}", "page", h, author, source=key, initials=initials)
                (added_doc_ids if inserted else changed_doc_ids).append(doc_id)
                ingest._set(source_id, phase="embedding")
                n, e = ingest._sync_chunks(conn, doc_id, title, body, max_tokens, overlap)
                stats["items_changed"] += 1
                stats["chunks"] += n
                stats["embedded"] += e
                hashes[path] = h
                ingest._set(source_id, done=done)
                if done % 5 == 0:
                    _checkpoint(conn, provider_col, display, "embedded", done, total,
                                new_cursor or "", "running", started)
                    conn.commit()

            # —— deletes: on a full resync, docs whose items vanished ——
            ingest._set(source_id, phase="indexing")
            if full:
                rows = conn.execute(
                    "SELECT id, source_path FROM documents WHERE source_id = %s", (source_id,)).fetchall()
                gone = []
                for r in rows:
                    sp = r["source_path"] or ""
                    rel = sp[len(key) + 1:] if sp.startswith(f"{key}/") else None
                    if rel is None or rel not in seen_paths:
                        gone.append(r["id"])
                if gone:
                    ingest._delete_documents(conn, gone)
                    stats["files_deleted"] = len(gone)
                hashes = {p: hv for p, hv in hashes.items() if p in seen_paths}

            # —— persist cursor + hash map, stamp truth ——
            cfg.update({"provider_key": key, "cursor": new_cursor or "", "item_hashes": hashes,
                        "last_sync_at": sync_start_iso, "last_error": ""})
            doc_count = conn.execute("SELECT count(*) AS n FROM documents WHERE source_id = %s",
                                     (source_id,)).fetchone()["n"]
            conn.execute("""UPDATE sources SET config = %s, last_sync_at = now(), docs_count = %s,
                            stat_num = %s, stat_unit = 'docs', health = 'Healthy', status = 'active'
                            WHERE id = %s""",
                         (json.dumps(cfg), doc_count, str(doc_count), source_id))
            detail = (f"{stats['items_changed']} items changed · {stats['files_deleted']} removed · "
                      f"{stats['chunks']} chunks · {stats['embedded']} embedded · "
                      f"{stats['skipped']} unchanged (hash skip)")
            _event(conn, provider_col, f"sync: {display}", detail)
            conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                         (f"{author} sync", f"synced {stats['items_changed']} items", display))
            _checkpoint(conn, provider_col, display, "indexed", done, total,
                        new_cursor or "", "done", started)
            conn.commit()

        # —— link extraction + document triggers: best-effort, like github ——
        touched = added_doc_ids + changed_doc_ids
        try:
            if touched:
                counts = links.extract(source_id, touched)
                with ingest._conn() as conn:
                    _event(conn, provider_col, f"links: {display}",
                           f"{counts['references']} references · {counts['links_to']} links_to · "
                           f"{counts['similar']} similar (edges created, {len(touched)} docs scanned)")
                    conn.commit()
        except Exception as le:  # noqa: BLE001 — link extraction is best-effort
            with ingest._conn() as conn:
                _event(conn, provider_col, f"links error: {display}", str(le)[:300])
                conn.commit()
        try:
            fired = (flowengine.fire_document_triggers(added_doc_ids, "document_added") +
                     flowengine.fire_document_triggers(changed_doc_ids, "document_changed"))
            if fired:
                with ingest._conn() as conn:
                    _event(conn, provider_col, f"triggers: {display}",
                           f"{len(fired)} flow run(s) auto-started "
                           f"({len(added_doc_ids)} added, {len(changed_doc_ids)} changed docs)")
                    conn.commit()
        except Exception as te:  # noqa: BLE001 — triggers are best-effort
            with ingest._conn() as conn:
                _event(conn, provider_col, f"trigger error: {display}", str(te)[:300])
                conn.commit()

        ingest._set(source_id, state="idle", phase="done", done=done, total=total, error="")
        # flow step reads files_changed too — connectors count everything as items
        return {**stats, "files_changed": 0}
    except Exception as e:  # noqa: BLE001 — a sync must always land in a truthful state
        msg = str(e)[:300]
        ingest._set(source_id, state="error", phase="", error=msg)
        try:
            with ingest._conn() as conn:
                cfg["last_error"] = msg
                conn.execute("UPDATE sources SET config = %s, health = 'Error' WHERE id = %s",
                             (json.dumps(cfg), source_id))
                _event(conn, provider_col, f"sync failed: {provider_col}", msg)
                _checkpoint(conn, provider_col, provider_col, "fetched", 0, 1, "", "paused", started)
                conn.commit()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
        return {**stats, "error": msg}

"""Mari — real GitHub ingestion pipeline (GITHUB-SYNC-CONTRACT.md).

Diff-based sync per source, run on a daemon thread (model: flowengine.start_run):
tree at HEAD → path filter → blob-sha diff → fetch only changed files → upsert
documents → chunk → sha256 per chunk → embed only changed chunks → mean-pool
doc embedding → delete docs for removed files → harvest issues/PRs/comments/
commit messages (incremental via `since`) → advance cursor, stamp last_sync_at,
write truthful sync_events + ingest_checkpoints + events rows.

An in-memory per-source registry powers the syncStatus GraphQL query.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import statistics
import threading
import time

import psycopg
from psycopg.rows import dict_row

import flowengine
import github
import links
import llm
from excerpt import excerpt

DB_URL_REF: dict = {"url": "postgresql://localhost/mari_cloud"}

ACTOR = "GitHub sync"
PROCESS_START_TS = time.time()  # for startup reconciliation — never touch newer rows
DEFAULT_PATHS = "**/*.md, **/*.mdx, README*, **/README*"
BOT_MARKERS = ("[bot]", "dependabot", "renovate", "github-actions")
COMMENT_CAP = 30  # latest N comments per issue/PR


def _conn():
    return psycopg.connect(DB_URL_REF["url"], row_factory=dict_row)


# ————————————————— status registry —————————————————

_LOCK = threading.Lock()
_STATUS: dict[int, dict] = {}
_IDLE = {"state": "idle", "phase": "", "done": 0, "total": 0, "error": ""}


def _set(source_id: int, **kw) -> None:
    with _LOCK:
        _STATUS.setdefault(source_id, dict(_IDLE)).update(kw)


def status(source_id: int) -> dict:
    with _LOCK:
        return dict(_STATUS.get(source_id) or _IDLE)


# ————————————————— chunking —————————————————


def _chunk_settings() -> tuple[int, int]:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'chunking'").fetchone()
    cfg = (row["value"] if row else {}) or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    d = cfg.get("default", {})
    return int(d.get("max_tokens", 512)), int(d.get("overlap", 64))


def chunk_text(text: str, max_tokens: int, overlap: int) -> list[str]:
    """Word-based approximation of token chunking (~0.75 words/token)."""
    words = text.split()
    size = max(int(max_tokens * 0.75), 32)
    step = max(size - int(overlap * 0.75), size // 2)
    if len(words) <= size:
        return [" ".join(words)] if words else []
    return [" ".join(words[i:i + size]) for i in range(0, len(words), step) if words[i:i + size]]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("# ").strip() or fallback
    return fallback


def _match(path: str, patterns: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    for pat in (p.strip() for p in patterns.split(",") if p.strip()):
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat):
            return True
        # '**/*.md' should also match top-level 'foo.md'
        if pat.startswith("**/") and fnmatch.fnmatch(path, pat[3:]):
            return True
    return False


def _is_bot(login: str) -> bool:
    login = (login or "").lower()
    return any(m in login for m in BOT_MARKERS)


# ————————————————— document + chunk upserts —————————————————


def _upsert_document(conn, source_id: int, external_id: str, title: str, body: str,
                     source_path: str, kind: str, content_hash: str, author: str,
                     source: str = "github", initials: str = "GH") -> tuple[int, bool]:
    """Upsert one document. Returns (doc_id, inserted) — inserted is True for a
    brand-new row, False for an update (xmax = 0 only on fresh inserts).
    `source`/`initials` default to github; connect_sync passes the provider key."""
    snippet = excerpt(body, title)
    row = conn.execute("""
        INSERT INTO documents (source, external_id, title, snippet, body, author, author_initials,
                               kind, updated_src, created_src, content_hash, source_path, source_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, CURRENT_DATE, %s, %s, %s)
        ON CONFLICT (source, external_id) DO UPDATE SET
          title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
          author = EXCLUDED.author, kind = EXCLUDED.kind, updated_src = CURRENT_DATE,
          content_hash = EXCLUDED.content_hash, source_path = EXCLUDED.source_path,
          source_id = EXCLUDED.source_id
        RETURNING id, (xmax = 0) AS inserted""",
        (source, external_id, title, snippet, body, author, initials, kind, content_hash, source_path, source_id)
    ).fetchone()
    conn.commit()
    return row["id"], bool(row["inserted"])


def _sync_chunks(conn, doc_id: int, title: str, body: str,
                 max_tokens: int, overlap: int) -> tuple[int, int]:
    """Chunk + hash + embed-only-changed. Returns (chunks, newly_embedded)."""
    pieces = chunk_text(f"{title}\n\n{body}", max_tokens, overlap)
    existing = {r["idx"]: r["content_hash"] for r in conn.execute(
        "SELECT idx, content_hash FROM chunks WHERE document_id = %s", (doc_id,)).fetchall()}
    embedded = 0
    for idx, piece in enumerate(pieces):
        h = _sha(piece)
        if existing.get(idx) == h:
            continue
        vec = llm.embed(piece)
        if vec:
            embedded += 1
        conn.execute("""
            INSERT INTO chunks (document_id, idx, content, content_hash, embedding)
            VALUES (%s, %s, %s, %s, %s::vector)
            ON CONFLICT (document_id, idx) DO UPDATE SET
              content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
              embedding = EXCLUDED.embedding""",
            (doc_id, idx, piece, h, str(vec) if vec else None))
    conn.execute("DELETE FROM chunks WHERE document_id = %s AND idx >= %s", (doc_id, len(pieces)))
    # doc-level embedding = mean of chunk embeddings (keeps existing doc search working)
    vecs = [r["embedding"] for r in conn.execute(
        "SELECT embedding::text AS embedding FROM chunks WHERE document_id = %s AND embedding IS NOT NULL",
        (doc_id,)).fetchall()]
    if vecs:
        parsed = [json.loads(v) for v in vecs]
        mean = [statistics.fmean(col) for col in zip(*parsed)]
        conn.execute("UPDATE documents SET embedding = %s::vector WHERE id = %s", (str(mean), doc_id))
    conn.commit()
    return len(pieces), embedded


def _delete_documents(conn, doc_ids: list[int]) -> None:
    if not doc_ids:
        return
    for table, col in (("tags", "document_id"), ("findings", "document_id"), ("changes", "document_id"),
                       ("watches", "document_id")):
        conn.execute(f"DELETE FROM {table} WHERE {col} = ANY(%s)", (doc_ids,))
    conn.execute("DELETE FROM edges WHERE from_doc = ANY(%s) OR to_doc = ANY(%s)", (doc_ids, doc_ids))
    conn.execute("DELETE FROM documents WHERE id = ANY(%s)", (doc_ids,))
    conn.commit()


# ————————————————— the sync worker —————————————————


def _sync_worker(source_id: int, full: bool) -> dict:
    """Run one sync. Returns the honest stats dict (plus 'error' on failure) so
    synchronous callers (flow sync_source steps) can report real numbers."""
    started = time.time()
    stats = {"files_changed": 0, "files_deleted": 0, "items_changed": 0,
             "chunks": 0, "embedded": 0, "skipped": 0}
    added_doc_ids: list[int] = []    # brand-new documents this batch
    changed_doc_ids: list[int] = []  # updated documents this batch
    with _conn() as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()
    if not src or src.get("kind") != "github":
        _set(source_id, state="error", phase="", error="not a github source")
        return {**stats, "error": "not a github source"}
    cfg = src["config"] if isinstance(src["config"], dict) else json.loads(src["config"] or "{}")
    repo, provider = cfg.get("repo", ""), src["provider"]
    patterns = cfg.get("paths") or DEFAULT_PATHS
    sync_start_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def checkpoint(stage: str, done: int, total: int, cursor: str, cp_status: str) -> None:
        with _conn() as c:
            c.execute("""
                INSERT INTO ingest_checkpoints (provider, item, stage, progress, total, cursor_id, duration, status, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (provider, item) DO UPDATE SET stage = EXCLUDED.stage,
                  progress = EXCLUDED.progress, total = EXCLUDED.total, cursor_id = EXCLUDED.cursor_id,
                  duration = EXCLUDED.duration, status = EXCLUDED.status, updated_at = now()""",
                (provider, repo, stage, done, max(total, 1), cursor,
                 time.strftime("%H:%M:%S", time.gmtime(time.time() - started)), cp_status))
            c.commit()

    token_state = github.push_token(str(cfg.get("token") or ""))
    try:
        max_tokens, overlap = _chunk_settings()
        if full:
            cfg["shas"], cfg["cursor"], cfg["items_since"] = {}, "", ""
            with _conn() as conn:
                conn.execute("""DELETE FROM chunks WHERE document_id IN
                                (SELECT id FROM documents WHERE source_id = %s)""", (source_id,))
                conn.execute("UPDATE documents SET content_hash = '' WHERE source_id = %s", (source_id,))
                # persist the cleared cursor immediately so an interrupted rebuild
                # resumes as a full pass instead of a bogus "unchanged" diff
                conn.execute("UPDATE sources SET config = %s WHERE id = %s", (json.dumps(cfg), source_id))
                conn.commit()

        # —— phase: listing ——
        _set(source_id, state="running", phase="listing", done=0, total=0, error="")
        branch = cfg.get("branch") or github.default_branch(repo)
        cfg["branch"] = branch
        head = github.head_sha(repo, branch)
        tree = github.get_tree(repo, head)
        wanted = {n["path"]: n["sha"] for n in tree if _match(n["path"], patterns)}
        stored: dict = cfg.get("shas") or {}
        changed = [p for p, sha in wanted.items() if stored.get(p) != sha]
        removed = [p for p in stored if p not in wanted]

        # —— items: issues / PRs (incremental via since) + commits ——
        items_since = cfg.get("items_since", "")
        issues_raw, issues_trunc = github.list_issues(repo, items_since)
        commits_raw, commits_trunc = github.list_commits(repo, branch, items_since)
        issues = [i for i in issues_raw
                  if not _is_bot((i.get("user") or {}).get("login", "")) and (i.get("title") or "").strip()]
        commits = [c for c in commits_raw
                   if not _is_bot(((c.get("author") or {}).get("login") or
                                   (c.get("commit", {}).get("author") or {}).get("name", "")))]
        commits = [c for c in commits
                   if not (c["commit"]["message"].startswith("Merge") and "\n" not in c["commit"]["message"].strip())]
        total = len(changed) + len(issues) + len(commits)
        _set(source_id, total=total)
        checkpoint("fetched", 0, total, head[:7], "running")
        done = 0

        # —— files: fetch → upsert → chunk → embed ——
        with _conn() as conn:
            for path in changed:
                _set(source_id, phase="fetching", done=done)
                text = github.get_blob(repo, wanted[path])
                if not text.strip():
                    stored[path] = wanted[path]
                    done += 1
                    continue
                _set(source_id, phase="chunking")
                doc_id, inserted = _upsert_document(conn, source_id, f"gh:{repo}:{path}",
                                                    _title_of(text, path.rsplit("/", 1)[-1]), text,
                                                    path, "page", wanted[path], ACTOR)
                (added_doc_ids if inserted else changed_doc_ids).append(doc_id)
                _set(source_id, phase="embedding")
                n, e = _sync_chunks(conn, doc_id, _title_of(text, path), text, max_tokens, overlap)
                stats["files_changed"] += 1
                stats["chunks"] += n
                stats["embedded"] += e
                stored[path] = wanted[path]
                done += 1
                _set(source_id, done=done)
                if done % 5 == 0:
                    checkpoint("embedded", done, total, head[:7], "running")
            stats["skipped"] += len(wanted) - len(changed)

            # Every item's stored content hash, in one round trip (SQL-4).
            # The loops below used to issue a SELECT per issue, per PR and per
            # commit to ask "did this change?" — roughly 5,000 round trips on a
            # busy repo, to answer a question one query answers. The file loop
            # above never had this problem because it already keeps the same
            # map in the source config (`cfg['shas']`); the item loops just
            # never got the same treatment.
            item_hashes = {
                r["external_id"]: r["content_hash"]
                for r in conn.execute(
                    """SELECT external_id, content_hash FROM documents
                       WHERE source_id = %s
                         AND (source_path LIKE 'issues/%%' OR source_path LIKE 'pulls/%%'
                              OR source_path LIKE 'commits/%%')""",
                    (source_id,)).fetchall()}

            # —— items: issues / PRs (+ comments) ——
            for issue in issues:
                _set(source_id, phase="fetching", done=done)
                number = issue["number"]
                kind = "pr" if "pull_request" in issue else "issue"
                parts = [f"# {issue['title']}",
                         f"{kind.upper()} #{number} · {(issue.get('user') or {}).get('login', '?')} · "
                         f"{issue.get('state', '')} · updated {issue.get('updated_at', '')}",
                         (issue.get("body") or "").strip()]
                if issue.get("comments", 0):
                    try:
                        for cm in github.list_issue_comments(repo, number, COMMENT_CAP):
                            if _is_bot((cm.get("user") or {}).get("login", "")) or not (cm.get("body") or "").strip():
                                continue
                            parts.append(f"--- comment by {(cm.get('user') or {}).get('login', '?')} "
                                         f"({cm.get('created_at', '')}):\n{cm['body'].strip()}")
                    except github.GithubError:
                        pass  # comments are best-effort
                body = "\n\n".join(p for p in parts if p)
                h = _sha(body)
                path = f"{'pulls' if kind == 'pr' else 'issues'}/{number}"
                done += 1
                if item_hashes.get(f"gh:{repo}:{path}") == h:
                    stats["skipped"] += 1
                    _set(source_id, done=done)
                    continue
                doc_id, inserted = _upsert_document(conn, source_id, f"gh:{repo}:{path}", issue["title"], body,
                                                    path, kind, h, (issue.get("user") or {}).get("login", ACTOR))
                (added_doc_ids if inserted else changed_doc_ids).append(doc_id)
                _set(source_id, phase="embedding")
                n, e = _sync_chunks(conn, doc_id, issue["title"], body, max_tokens, overlap)
                stats["items_changed"] += 1
                stats["chunks"] += n
                stats["embedded"] += e
                _set(source_id, done=done)

            # —— items: commit messages ——
            for c in commits:
                msg = c["commit"]["message"].strip()
                sha = c["sha"]
                title = msg.splitlines()[0][:140]
                author = (c.get("author") or {}).get("login") or c["commit"]["author"].get("name", "?")
                body = f"# {title}\n\ncommit {sha[:12]} · {author} · {c['commit']['author'].get('date', '')}\n\n{msg}"
                h = _sha(body)
                path = f"commits/{sha[:12]}"
                done += 1
                if item_hashes.get(f"gh:{repo}:{path}") == h:
                    stats["skipped"] += 1
                    _set(source_id, done=done)
                    continue
                _set(source_id, phase="embedding")
                doc_id, inserted = _upsert_document(conn, source_id, f"gh:{repo}:{path}", title, body,
                                                    path, "commit", h, author)
                (added_doc_ids if inserted else changed_doc_ids).append(doc_id)
                n, e = _sync_chunks(conn, doc_id, title, body, max_tokens, overlap)
                stats["items_changed"] += 1
                stats["chunks"] += n
                stats["embedded"] += e
                _set(source_id, done=done)

            # —— deletes for removed files ——
            _set(source_id, phase="indexing")
            if removed:
                rows = conn.execute("""SELECT id FROM documents WHERE source_id = %s AND source_path = ANY(%s)""",
                                    (source_id, removed)).fetchall()
                _delete_documents(conn, [r["id"] for r in rows])
                stats["files_deleted"] = len(removed)
                for p in removed:
                    stored.pop(p, None)

            # —— advance cursor, stamp truth ——
            # If pagination hit the safety cap, hold items_since at the newest
            # item actually fetched so the next sync resumes past the cap
            # instead of silently skipping everything beyond it.
            next_since = sync_start_iso
            trunc_notes = []
            if issues_trunc and issues_raw:
                newest = max(i.get("updated_at") or "" for i in issues_raw)
                if newest:
                    next_since = min(next_since, newest)
                trunc_notes.append(f"issues/PRs hit the page cap ({len(issues_raw)} fetched)")
            if commits_trunc and commits_raw:
                newest = max(((c.get("commit") or {}).get("committer") or {}).get("date", "")
                             for c in commits_raw)
                if newest:
                    next_since = min(next_since, newest)
                trunc_notes.append(f"commits hit the page cap ({len(commits_raw)} fetched)")
            cfg.update({"shas": stored, "cursor": head, "items_since": next_since,
                        "last_sync_at": sync_start_iso, "last_error": ""})
            doc_count = conn.execute("SELECT count(*) AS n FROM documents WHERE source_id = %s",
                                     (source_id,)).fetchone()["n"]
            conn.execute("""UPDATE sources SET config = %s, last_sync_at = now(), docs_count = %s,
                            stat_num = %s, stat_unit = 'docs', health = 'Healthy', status = 'active'
                            WHERE id = %s""",
                         (json.dumps(cfg), doc_count, str(doc_count), source_id))
            detail = (f"{stats['files_changed']} files changed · {stats['items_changed']} issues/PRs/commits · "
                      f"{stats['files_deleted']} removed · {stats['chunks']} chunks · "
                      f"{stats['embedded']} embedded · {stats['skipped']} unchanged (hash skip)")
            conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                         (provider, f"sync: {repo} @ {head[:7]}", detail))
            conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                         (ACTOR, f"synced {stats['files_changed'] + stats['items_changed']} items", repo))
            if trunc_notes:
                conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                             (provider, f"sync truncated: {repo}",
                              "; ".join(trunc_notes) + f" — cursor held at {next_since} so the next sync resumes"))
            conn.commit()
        checkpoint("indexed", done, total, head[:7], "done")

        # —— link extraction (LINEAGE-ROLLUP-CONTRACT.md §2): cheap incremental
        # pass over just the docs added/changed this run (similarity for those
        # docs still compares against the whole source). Runs BEFORE triggers
        # so triggered flows see the edges; never breaks the sync itself. ——
        try:
            touched = added_doc_ids + changed_doc_ids
            if touched:
                counts = links.extract(source_id, touched)
                with _conn() as conn:
                    conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                                 (provider, f"links: {repo}",
                                  f"{counts['references']} references · {counts['links_to']} links_to · "
                                  f"{counts['similar']} similar (edges created, {len(touched)} docs scanned)"))
                    conn.commit()
        except Exception as le:  # noqa: BLE001 — link extraction is best-effort
            with _conn() as conn:
                conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                             (provider, f"links error: {repo}", str(le)[:300]))
                conn.commit()

        # —— document triggers: fire AFTER embeddings/commit so flow steps see
        # complete docs. A trigger failure must never break the sync itself. ——
        try:
            fired = (flowengine.fire_document_triggers(added_doc_ids, "document_added") +
                     flowengine.fire_document_triggers(changed_doc_ids, "document_changed"))
            if fired:
                with _conn() as conn:
                    conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                                 (provider, f"triggers: {repo}",
                                  f"{len(fired)} flow run(s) auto-started "
                                  f"({len(added_doc_ids)} added, {len(changed_doc_ids)} changed docs)"))
                    conn.commit()
        except Exception as te:  # noqa: BLE001 — triggers are best-effort
            with _conn() as conn:
                conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                             (provider, f"trigger error: {repo}", str(te)[:300]))
                conn.commit()

        _set(source_id, state="idle", phase="done", done=done, total=total, error="")
        return stats
    except Exception as e:  # noqa: BLE001 — sync must always land in a truthful state
        msg = str(e)[:300]
        _set(source_id, state="error", phase="", error=msg)
        with _conn() as conn:
            cfg["last_error"] = msg
            conn.execute("UPDATE sources SET config = %s, health = 'Error' WHERE id = %s",
                         (json.dumps(cfg), source_id))
            conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                         (provider, f"sync failed: {repo}", msg))
            conn.commit()
        checkpoint("fetched", 0, 1, "", "paused")
        return {**stats, "error": msg}
    finally:
        github.pop_token(token_state)


_RUNNING: set[int] = set()


def _worker_for(source_id: int):
    """Dispatch by sources.kind: 'github' → the worker above, 'connector' →
    connect_sync's generic worker (CONNECTORS-CONTRACT.md). Both share this
    module's status registry and _RUNNING guard, so syncStatus/syncSource/
    resyncSource and flow sync_source steps work unchanged for either kind."""
    with _conn() as conn:
        row = conn.execute("SELECT kind FROM sources WHERE id = %s", (source_id,)).fetchone()
    if row and row.get("kind") == "connector":
        import connect_sync  # late import — connect_sync imports ingest
        return connect_sync._sync_worker
    return _sync_worker


def _run_guarded(source_id: int, full: bool) -> dict:
    """Dispatch + run one sync with the _RUNNING slot released no matter how
    the worker exits — early returns, dispatch errors, and crashes included.
    This is the ONLY place the slot is released; workers never touch it."""
    try:
        return _worker_for(source_id)(source_id, full)
    finally:
        with _LOCK:
            _RUNNING.discard(source_id)


def start_sync(source_id: int, full: bool = False) -> bool:
    """Kick off a background sync; returns False if one is already running."""
    with _LOCK:
        if source_id in _RUNNING:
            return False
        _RUNNING.add(source_id)
    # mark running synchronously so a syncStatus poll right after the mutation
    # never sees a stale 'idle'
    _set(source_id, state="running", phase="listing", done=0, total=0, error="")
    threading.Thread(target=_run_guarded, args=(source_id, full), daemon=True).start()
    return True


def run_sync(source_id: int, full: bool = False) -> dict | None:
    """Synchronous sync for flow sync_source steps: runs the worker on the
    caller's thread and returns its honest stats dict ('error' key on failure).
    Returns None if a sync for this source is already running."""
    with _LOCK:
        if source_id in _RUNNING:
            return None
        _RUNNING.add(source_id)
    _set(source_id, state="running", phase="listing", done=0, total=0, error="")
    return _run_guarded(source_id, full)


# ————————————————— startup wiring —————————————————
#
# Periodic GitHub polling is no longer an env knob (MARI_GITHUB_POLL_MINUTES):
# each github source gets a schedule-triggered "Sync <repo>" flow with a
# sync_source step, visible and editable in the Flows UI. The public name is
# kept because app.py's startup path calls start_poller().

_POLLER = {"started": False}


def reconcile_stale_checkpoints() -> int:
    """Startup reconciliation: a checkpoint left 'running' by an unclean
    shutdown would show a phantom in-flight sync forever. Flip rows from
    BEFORE this process started to 'paused' (the error-path status) with a
    truthful sync_events note; rows this process wrote are never touched.
    Returns how many were flipped."""
    with _conn() as conn:
        rows = conn.execute(
            """UPDATE ingest_checkpoints SET status = 'paused', updated_at = now()
               WHERE status = 'running' AND updated_at < to_timestamp(%s)
               RETURNING provider, item""", (PROCESS_START_TS,)).fetchall()
        for r in rows:
            conn.execute("INSERT INTO sync_events (provider, event, detail, at_label) VALUES (%s, %s, %s, to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))",
                         (r["provider"], f"sync interrupted: {r['item']}",
                          "checkpoint was still 'running' at startup — interrupted by restart, marked paused"))
        conn.commit()
    return len(rows)


def start_poller() -> None:
    """Startup wiring: seed the scheduled sync/digest flows and start the flow
    scheduler. The schema it depends on is already applied by db.ensure_schema()
    (init.sql) earlier in startup. Guarded so uvicorn --reload / repeat imports
    don't double-start."""
    if _POLLER["started"]:
        return
    _POLLER["started"] = True

    try:
        reconcile_stale_checkpoints()
    except Exception:  # noqa: BLE001 — never block startup on reconciliation
        pass
    try:
        flowengine.seed_scheduled_flows()
    except Exception:  # noqa: BLE001 — never block startup on seeding
        pass
    flowengine.start_scheduler()

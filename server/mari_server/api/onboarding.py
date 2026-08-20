"""Mari — onboarding endpoints (file upload ingestion + glossary harvest).

POST /onboard/upload            multipart .md/.mdx/.markdown/.txt files → the same
                                document→chunk→hash→embed pipeline GitHub sync uses
                                (helpers imported from ingest.py; nothing faked).
POST /onboard/glossary-harvest  LLM (ollama JSON mode) proposes glossary candidates
                                grounded in the most-connected page documents;
                                malformed or unavailable model output returns no
                                candidates. Persists NOTHING — the UI reviews candidates
                                and calls the existing upsertGlossary mutation.
"""

from __future__ import annotations

import concurrent.futures as cf
import time

import psycopg
from fastapi import APIRouter, File, HTTPException, UploadFile
from psycopg.rows import dict_row
from pydantic import BaseModel

from mari_server.integrations import llm
from mari_server.api import access
from mari_components import KnowledgeDocument
from mari_components.knowledge import harvest_glossary as component_harvest_glossary
# Reuse the real GitHub ingestion pipeline pieces (chunk + content-hash + embed +
# mean-pooled doc embedding). ingest._upsert_document is github-specific
# (hardcodes source='github'), so the document upsert lives here instead.
from mari_server.integrations.document_index import chunk_settings, content_hash, sync_chunks, title_of

from mari_server import db as postgres
from mari_server.services.excerpt import excerpt

router = APIRouter(prefix="/onboard")

ALLOWED_EXT = {".md", ".mdx", ".markdown", ".txt"}
MAX_FILES = 20
MAX_BYTES = 1_000_000
ACTOR = "Upload"


def _conn():
    return postgres.connect()


# ————————————————— 1. file upload ingestion —————————————————


def _upload_source(conn) -> int:
    """Create or reuse the single 'upload' source (sources.provider is UNIQUE)."""
    row = conn.execute("""
        INSERT INTO sources (project_id, provider, display_name, kind, stat_unit, status, health)
        VALUES (%s, 'upload', 'Uploads', 'upload', 'docs', 'active', 'Healthy')
        ON CONFLICT (project_id, provider) DO UPDATE SET kind = 'upload', display_name = 'Uploads'
        RETURNING id""", (access.require_current_access().project_id,)).fetchone()
    conn.commit()
    return row["id"]


def _upsert_upload_document(conn, source_id: int, filename: str, text: str) -> int:
    """Same shape as ingest._upsert_document, with source='upload'."""
    title = title_of(text, filename)
    snippet = excerpt(text, title)
    row = conn.execute("""
        INSERT INTO documents (project_id, source, external_id, title, snippet, body, author, author_initials,
                               kind, updated_src, created_src, content_hash, source_path, source_id)
        VALUES (%s, 'upload', %s, %s, %s, %s, %s, 'UP', 'page', CURRENT_DATE, CURRENT_DATE, %s, %s, %s)
        ON CONFLICT (project_id, source, external_id) DO UPDATE SET
          title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
          updated_src = CURRENT_DATE, content_hash = EXCLUDED.content_hash,
          source_path = EXCLUDED.source_path, source_id = EXCLUDED.source_id
        RETURNING id""",
        (access.require_current_access().project_id, f"upload:{filename}", title, snippet, text, ACTOR, content_hash(text),
         f"upload/{filename}", source_id)).fetchone()
    conn.commit()
    return row["id"]


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"at most {MAX_FILES} files per upload")
    max_tokens, overlap = chunk_settings()
    results: list[dict] = []
    # A document is keyed on its flattened filename (the endpoint never sees
    # the client's folder structure), so two files from different folders that
    # happen to share a name — e.g. guides/rules.md and reference/rules.md —
    # are indistinguishable to it. Across separate uploads that is the desired
    # "resync the same file" behaviour; within ONE batch it can only mean two
    # different files collided, and upserting both in turn would silently
    # discard the first one's content with no sign anything was lost.
    seen_names: set[str] = set()
    with _conn() as conn:
        source_id = _upload_source(conn)
        for f in files:
            name = (f.filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if not name or ext not in ALLOWED_EXT:
                results.append({"name": name or "?", "docId": None, "chunks": 0,
                                "embedded": 0, "error": f"unsupported type (allowed: {', '.join(sorted(ALLOWED_EXT))})"})
                continue
            if name in seen_names:
                results.append({"name": name, "docId": None, "chunks": 0, "embedded": 0,
                                "error": "duplicate filename in this upload (files are matched by name only, "
                                         "with no folder path) — rename one and upload it separately"})
                continue
            seen_names.add(name)
            # Bound memory before decoding. UploadFile may be backed by disk,
            # but an unrestricted read still materializes attacker-controlled
            # input in the API process.
            raw = await f.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                results.append({"name": name, "docId": None, "chunks": 0,
                                "embedded": 0, "error": "file exceeds 1MB"})
                continue
            text = raw.decode("utf-8", errors="replace")
            if not text.strip():
                results.append({"name": name, "docId": None, "chunks": 0,
                                "embedded": 0, "error": "empty file"})
                continue
            doc_id = _upsert_upload_document(conn, source_id, name, text)
            # chunk + per-chunk content hash + embed only changed (ingest pipeline):
            # re-uploading unchanged content embeds nothing.
            n, e = sync_chunks(conn, doc_id, title_of(text, name), text, max_tokens, overlap)
            results.append({"name": name, "docId": doc_id, "chunks": n, "embedded": e})

        ok_files = [r for r in results if r.get("docId")]
        doc_count = conn.execute("SELECT count(*) AS n FROM documents WHERE source_id = %s",
                                 (source_id,)).fetchone()["n"]
        conn.execute("""UPDATE sources SET last_sync_at = now(), docs_count = %s,
                        stat_num = %s, stat_unit = 'docs' WHERE id = %s""",
                     (doc_count, str(doc_count), source_id))
        detail = (f"{len(ok_files)} file(s) ingested · "
                  f"{sum(r['chunks'] for r in ok_files)} chunks · "
                  f"{sum(r['embedded'] for r in ok_files)} embedded · "
                  f"{sum(1 for r in ok_files if r['embedded'] == 0)} unchanged (hash skip)")
        conn.execute("""INSERT INTO sync_events (provider, event, detail, at_label)
                        VALUES ('upload', %s, %s,
                                to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))""",
                     (f"upload: {len(files)} file(s)", detail))
        conn.execute("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
                     (ACTOR, f"uploaded {len(ok_files)} file(s)", "Uploads"))
        conn.commit()
    return {"ok": True, "sourceId": source_id, "files": results}


# ————————————————— 2. LLM glossary harvest —————————————————


class HarvestIn(BaseModel):
    sourceId: int | None = None
    limit: int = 15


HARVEST_SYSTEM = (
    "You extract glossary candidates from a team's documentation. A good glossary "
    "term is a domain-specific concept, product name, or piece of jargon a new "
    "teammate would need defined — not a generic word. Definitions must be one "
    "sentence and grounded ONLY in what the provided text says."
)

_BATCH = 4          # docs per LLM call
_EXCERPT = 1500     # chars of body per doc

# FLOW-8: `limit` defaults to 15 and accepts up to 50, so at 4 documents per
# call this made up to 13 model calls — sequentially, at the 120-second default
# timeout, inside one HTTP request. Twenty-six minutes, on a POST the browser
# gave up on long before.
#
# The calls are independent (each reads its own batch of documents), so they run
# concurrently on a small bounded pool, each with its own shorter timeout, under
# a wall-clock deadline for the whole endpoint. The response says which batches
# were read, so a harvest that hit the deadline is a harvest someone can re-run
# knowing that — not a shorter list of candidates that looks like a thin corpus.
_HARVEST_WORKERS = 4
_HARVEST_CALL_TIMEOUT = 45.0
_HARVEST_DEADLINE = 90.0


def _harvest_docs(source_id: int | None, limit: int) -> list[dict]:
    """Most-connected, then most-recent, page documents (optionally one source).

    The degree used to be a correlated `count(*)` over `edges` evaluated once
    per document, and because the ORDER BY sorts on it, the LIMIT could not push
    down: every page document paid a full edge scan before the first row was
    discarded (SQL-4). `degree` aggregates the edge table once, in a single pass
    over both endpoints, and joins in. A document nothing links to is absent
    from it and coalesces to 0 — the same answer count(*) gave it."""
    where = "d.kind = 'page' AND d.body <> ''"
    args: list = []
    if source_id is not None:
        where += " AND d.source_id = %s"
        args.append(source_id)
    args.append(limit)
    with _conn() as conn:
        return conn.execute(f"""
            WITH degree AS (
              SELECT doc, count(*) AS n FROM (
                SELECT from_doc AS doc FROM edges
                UNION ALL
                SELECT to_doc AS doc FROM edges
              ) x GROUP BY doc
            )
            SELECT d.id, d.title, d.body, coalesce(g.n, 0) AS degree
            FROM documents d
            LEFT JOIN degree g ON g.doc = d.id
            WHERE {where}
            ORDER BY degree DESC, d.updated_src DESC NULLS LAST, d.id DESC
            LIMIT %s""", tuple(args)).fetchall()


def _existing_terms() -> set[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT term, variants FROM glossary").fetchall()
    seen: set[str] = set()
    for r in rows:
        seen.add(r["term"].strip().lower())
        for v in (r["variants"] or "").split(","):
            if v.strip():
                seen.add(v.strip().lower())
    return seen


def _llm_batch(docs: list[dict]) -> list[dict]:
    """Run the reusable strict glossary recipe over one bounded batch."""
    documents = [KnowledgeDocument(
        str(doc["id"]), doc["title"], doc["body"][:_EXCERPT],
    ) for doc in docs]
    by_id = {str(doc["id"]): doc for doc in docs}
    candidates = component_harvest_glossary(
        documents,
        generate_json=lambda prompt, _version: llm.generate_json(
            prompt, HARVEST_SYSTEM, _HARVEST_CALL_TIMEOUT),
        maximum_documents=len(documents),
        maximum_characters=len(documents) * _EXCERPT,
    )
    return [{
        "term": candidate.term,
        "definition": candidate.definition,
        "evidence": by_id[candidate.evidence[0].document_id]["title"],
    } for candidate in candidates]


@router.post("/glossary-harvest")
def glossary_harvest(body: HarvestIn):
    limit = max(1, min(body.limit, 50))
    docs = _harvest_docs(body.sourceId, limit)
    if not docs:
        return {"candidates": [], "llm": False}
    existing = _existing_terms()

    candidates: list[dict] = []
    seen: set[str] = set()
    llm_ok = False

    def keep(c: dict) -> None:
        key = c["term"].lower()
        if key in existing or key in seen:  # dedupe: glossary rows + within-batch
            return
        seen.add(key)
        candidates.append(c)

    batches = [docs[i:i + _BATCH] for i in range(0, len(docs), _BATCH)]
    documents_read = 0
    deadline = time.monotonic() + _HARVEST_DEADLINE
    with cf.ThreadPoolExecutor(max_workers=min(_HARVEST_WORKERS, len(batches)),
                               thread_name_prefix="mari-harvest") as pool:
        futures = {pool.submit(_llm_batch, batch): batch for batch in batches}
        for future in cf.as_completed(futures):
            batch = futures[future]
            documents_read += len(batch)
            try:
                raw = future.result()
            except Exception:  # noqa: BLE001 — one bad batch is not a failed harvest
                raw = None
            llm_ok = True
            for cand in raw[:12]:
                keep(cand)
            if time.monotonic() >= deadline:
                for pending in futures:
                    if not pending.done():
                        pending.cancel()
                break

    if not llm_ok:
        return {"candidates": candidates[:25], "llm": False,
                "documentsRead": 0, "documentsTotal": len(docs)}
    # documentsRead vs documentsTotal is how the caller can tell a corpus with
    # few terms in it from a harvest the deadline cut short.
    return {"candidates": candidates[:25], "llm": True,
            "documentsRead": documents_read,
            "documentsTotal": len(docs)}

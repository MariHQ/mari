"""Mari Cloud — onboarding endpoints (file upload ingestion + glossary harvest).

POST /onboard/upload            multipart .md/.mdx/.markdown/.txt files → the same
                                document→chunk→hash→embed pipeline GitHub sync uses
                                (helpers imported from ingest.py; nothing faked).
POST /onboard/glossary-harvest  LLM (ollama JSON mode) proposes glossary candidates
                                grounded in the most-connected page documents;
                                deterministic capitalized-phrase fallback when ollama
                                is down. Persists NOTHING — the UI reviews candidates
                                and calls the existing upsertGlossary mutation.
"""

from __future__ import annotations

import json
import re

import psycopg
from fastapi import APIRouter, File, HTTPException, UploadFile
from psycopg.rows import dict_row
from pydantic import BaseModel

import llm
# Reuse the real GitHub ingestion pipeline pieces (chunk + content-hash + embed +
# mean-pooled doc embedding). ingest._upsert_document is github-specific
# (hardcodes source='github'), so the document upsert lives here instead.
from ingest import _chunk_settings, _sha, _sync_chunks, _title_of

from db import DB_URL

router = APIRouter(prefix="/onboard")

ALLOWED_EXT = {".md", ".mdx", ".markdown", ".txt"}
MAX_FILES = 20
MAX_BYTES = 1_000_000
ACTOR = "Upload"


def _conn():
    return psycopg.connect(DB_URL, row_factory=dict_row)


# ————————————————— 1. file upload ingestion —————————————————


def _upload_source(conn) -> int:
    """Create or reuse the single 'upload' source (sources.provider is UNIQUE)."""
    row = conn.execute("""
        INSERT INTO sources (provider, display_name, kind, stat_unit, status, health)
        VALUES ('upload', 'Uploads', 'upload', 'docs', 'active', 'Healthy')
        ON CONFLICT (provider) DO UPDATE SET kind = 'upload', display_name = 'Uploads'
        RETURNING id""").fetchone()
    conn.commit()
    return row["id"]


def _upsert_upload_document(conn, source_id: int, filename: str, text: str) -> int:
    """Same shape as ingest._upsert_document, with source='upload'."""
    title = _title_of(text, filename)
    snippet = " ".join(text.split())[:180]
    row = conn.execute("""
        INSERT INTO documents (source, external_id, title, snippet, body, author, author_initials,
                               kind, updated_src, created_src, content_hash, source_path, source_id)
        VALUES ('upload', %s, %s, %s, %s, %s, 'UP', 'page', CURRENT_DATE, CURRENT_DATE, %s, %s, %s)
        ON CONFLICT (source, external_id) DO UPDATE SET
          title = EXCLUDED.title, snippet = EXCLUDED.snippet, body = EXCLUDED.body,
          updated_src = CURRENT_DATE, content_hash = EXCLUDED.content_hash,
          source_path = EXCLUDED.source_path, source_id = EXCLUDED.source_id
        RETURNING id""",
        (f"upload:{filename}", title, snippet, text, ACTOR, _sha(text),
         f"upload/{filename}", source_id)).fetchone()
    conn.commit()
    return row["id"]


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"at most {MAX_FILES} files per upload")
    max_tokens, overlap = _chunk_settings()
    results: list[dict] = []
    with _conn() as conn:
        source_id = _upload_source(conn)
        for f in files:
            name = (f.filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if not name or ext not in ALLOWED_EXT:
                results.append({"name": name or "?", "docId": None, "chunks": 0,
                                "embedded": 0, "error": f"unsupported type (allowed: {', '.join(sorted(ALLOWED_EXT))})"})
                continue
            raw = await f.read()
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
            n, e = _sync_chunks(conn, doc_id, _title_of(text, name), text, max_tokens, overlap)
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


def _harvest_docs(source_id: int | None, limit: int) -> list[dict]:
    """Most-connected, then most-recent, page documents (optionally one source)."""
    where = "d.kind = 'page' AND d.body <> ''"
    args: list = []
    if source_id is not None:
        where += " AND d.source_id = %s"
        args.append(source_id)
    args.append(limit)
    with _conn() as conn:
        return conn.execute(f"""
            SELECT d.id, d.title, d.body,
                   (SELECT count(*) FROM edges e WHERE e.from_doc = d.id OR e.to_doc = d.id) AS degree
            FROM documents d
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


def _llm_batch(docs: list[dict]) -> list[dict] | None:
    """One LLM call over a batch of doc excerpts → raw candidate list (or None)."""
    excerpts = "\n\n".join(
        f"--- Document: {d['title']}\n{d['body'][:_EXCERPT]}" for d in docs)
    titles = [d["title"] for d in docs]
    prompt = (
        f"Documents:\n\n{excerpts}\n\n"
        "List up to 6 glossary-worthy domain terms that actually appear in these "
        "documents. For each, return an object with:\n"
        '  "term": the term as written in the text,\n'
        '  "definition": ONE sentence defining it, grounded strictly in the text,\n'
        f'  "evidence": the exact title of the document it came from (one of {json.dumps(titles)}).\n'
        "Skip generic words. Return a JSON array."
    )
    out = llm.generate_json(prompt, HARVEST_SYSTEM)
    return out if isinstance(out, list) else None


def _validate(cand: dict, docs: list[dict]) -> dict | None:
    """Strict validation: shapes, lengths, term actually appears in a batch doc,
    evidence is a real doc title (repaired to the doc containing the term)."""
    if not isinstance(cand, dict):
        return None
    term = str(cand.get("term") or "").strip()
    definition = str(cand.get("definition") or "").strip()
    evidence = str(cand.get("evidence") or "").strip()
    if not (2 <= len(term) <= 80) or len(definition) < 15 or len(definition) > 400:
        return None
    holder = next((d for d in docs if term.lower() in d["body"].lower()
                   or term.lower() in d["title"].lower()), None)
    if holder is None:
        return None  # not grounded — the model invented it
    titles = {d["title"] for d in docs}
    if evidence not in titles:
        evidence = holder["title"]
    return {"term": term, "definition": definition.rstrip() if definition.endswith(".")
            else definition.rstrip() + ".", "evidence": evidence}


_PHRASE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+(?:[ -][A-Z][A-Za-z0-9]+)+)\b")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STOP_STARTS = ("The ", "This ", "That ", "These ", "Those ", "A ", "An ", "In ", "If ",
                "For ", "When ", "How ", "What ", "Why ", "See ", "Note ", "It ")


def _fallback(docs: list[dict]) -> list[dict]:
    """Deterministic harvest when ollama is down: frequent capitalized multiword
    phrases + the sentence where each first appears."""
    counts: dict[str, int] = {}
    first_use: dict[str, tuple[str, str]] = {}  # phrase -> (sentence, doc title)
    for d in docs:
        text = re.sub(r"[#*`>\[\]()|]", " ", d["body"])
        for sent in _SENT_SPLIT.split(text):
            sent = " ".join(sent.split())
            for m in _PHRASE_RE.finditer(sent):
                phrase = m.group(1).strip()
                if any(phrase.startswith(s) for s in _STOP_STARTS) or len(phrase) > 60:
                    continue
                counts[phrase] = counts.get(phrase, 0) + 1
                if phrase not in first_use and len(sent) >= 25:
                    first_use[phrase] = (sent[:300], d["title"])
    out = []
    for phrase, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n < 2 or phrase not in first_use:
            continue
        sent, title = first_use[phrase]
        out.append({"term": phrase,
                    "definition": sent if sent.endswith(".") else sent + ".",
                    "evidence": title})
    return out


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

    for i in range(0, len(docs), _BATCH):
        batch = docs[i:i + _BATCH]
        raw = _llm_batch(batch)
        if raw is None:
            continue
        llm_ok = True
        for cand in raw[:12]:
            v = _validate(cand, batch)
            if v:
                keep(v)

    if not llm_ok:  # ollama down → deterministic fallback, honestly flagged
        for c in _fallback(docs):
            keep(c)
        return {"candidates": candidates[:25], "llm": False}
    return {"candidates": candidates[:25], "llm": True}

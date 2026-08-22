"""Mari — knowledge mutations: docs, facts, decisions, answers, glossary,
tags, tasks, lineage, digest, insights, watches."""

from __future__ import annotations

import concurrent.futures as cf
import contextvars
import json
import time
import typing as t

from mari_server.providers import models as llm
from mari_server.identity import context as access
from mari_server.persistence.postgres import lineage as links
from mari_server.persistence.postgres import knowledge as knowledge_store
from mari_server.persistence.postgres.database import actor_name, audit
from mari_components import KnowledgeDocument
from mari_components.connectors import (
    GitHubConfig, github_issue_comments, github_pull_files, github_pull_request,
)
from mari_components.destinations import GitHubCommentTarget, post_github_comment
from mari_components.knowledge import (
    check_claims as component_check_claims,
    extract_decisions as component_extract_decisions,
    extract_facts as component_extract_facts,
    refine_document as component_refine_document,
    summarize_digest as component_summarize_digest,
    is_claim,
)


# ————————————————— the corpus scanners —————————————————
#
# Both scanners read recent documents through a model and file what they find.
# Four things about how they do it were wrong, and all four are the same shape
# of mistake — treating "call the model once per document" as free:
#
# FACT-1  Eight documents × one 120-second call each, sequentially, on the
#         thread serving a GraphQL mutation: up to sixteen minutes of a request
#         that any proxy in front of it would have given up on. The calls are
#         independent, so they run concurrently now, on a small bounded pool,
#         under a wall-clock deadline that ends the scan honestly rather than
#         letting it run until something else times out.
#
# FACT-2  A single per-scan claim budget, consumed in document order, meant the
#         first documents ate it and the rest were read for nothing — and since
#         documents were picked newest-first, "the rest" was the same set every
#         time. The ceiling is now per document, so no document's claims are
#         dropped because another document went first, and selection rotates on
#         documents.facts_scanned_at / .decisions_scanned_at (init.sql) so a
#         document that is never the newest is still eventually read.
#
# FACT-3  `f.get(...)` on whatever the model returned. A model that answers
#         with ["claim one", "claim two"] — a list of strings, which is a
#         perfectly ordinary thing for a model to do — raised AttributeError
#         and took the whole mutation down. Guarded now, the way the two
#         sibling scanners in this file already guarded.
#
# FACT-4  The flow's "Read recent documents" step computed a document list that
#         the scan then ignored in favour of re-running its own query. Both
#         scanners now take the list they are given, so the step means what its
#         label says and changing `k` in the flow editor changes the scan.

SCAN_DOCS = 8            # documents read per scan when the caller names none
CLAIMS_PER_DOC = 2       # ceiling per document — never a shared, racing budget
SCAN_WORKERS = 4         # concurrent model calls; the pool is bounded on purpose
SCAN_CALL_TIMEOUT = 60.0  # per model call
SCAN_DEADLINE = 180.0    # wall clock for the whole scan, however many documents

_EVIDENCE_SCHEMA = {
    "type": "array", "items": {"type": "object", "properties": {
        "document_id": {"type": "string"}, "quote": {"type": "string"},
    }, "required": ["document_id", "quote"]},
}
_FACT_SCHEMA = {"type": "object", "properties": {"facts": {
    "type": "array", "items": {"type": "object", "properties": {
        "claim": {"type": "string"}, "evidence": _EVIDENCE_SCHEMA,
    }, "required": ["claim", "evidence"]},
}}, "required": ["facts"]}
_DECISION_SCHEMA = {"type": "object", "properties": {"decisions": {
    "type": "array", "items": {"type": "object", "properties": {
        "statement": {"type": "string"}, "evidence": _EVIDENCE_SCHEMA,
    }, "required": ["statement", "evidence"]},
}}, "required": ["decisions"]}


def _scan_batch(kind: str, doc_ids: list[int] | None, limit: int) -> list[dict]:
    """The documents this scan should read.

    With `doc_ids` (a flow step's fetch_docs output) those documents, in the
    order given. Without, the least-recently-scanned documents first, newest
    breaking the tie — so every document is reached in turn instead of the two
    newest being re-read on every scan forever."""
    return knowledge_store.scan_documents(kind, doc_ids, limit)


def _mark_scanned(kind: str, doc_ids: list[int]) -> None:
    """Record that the scanner read these documents. This is what makes the
    next scan pick different ones; without it the rotation above is a no-op."""
    knowledge_store.mark_scanned(kind, doc_ids)


def _scan_concurrently(
    docs: list[dict], operation,
) -> tuple[list[tuple[dict, t.Any]], int, int]:
    """Run one model call per document, at most SCAN_WORKERS at a time, and
    stop accepting new work once SCAN_DEADLINE has passed.

    Returns (successful results, unread, failed). `unread` is how many documents
    the deadline cut off and `failed` is how many completed model calls raised.
    The caller reports both. A scan that ran out of time and said so is a
    scan someone can re-run; a scan that ran out of time and returned a smaller
    number is indistinguishable from a corpus with less in it."""
    if not docs:
        return [], 0, 0
    results: list[tuple[dict, t.Any]] = []
    failed = 0
    pool = cf.ThreadPoolExecutor(max_workers=min(SCAN_WORKERS, len(docs)),
                                 thread_name_prefix="mari-scan")
    futures = {
        pool.submit(contextvars.copy_context().run, operation, document): document
        for document in docs
    }
    done, pending = cf.wait(futures, timeout=SCAN_DEADLINE)
    for future in done:
        document = futures[future]
        try:
            results.append((document, future.result()))
        except Exception:  # noqa: BLE001 — one bad document must not end the scan
            failed += 1
    for future in pending:
        future.cancel()
    pool.shutdown(wait=False, cancel_futures=True)
    return results, len(pending), failed


def _scan_note(unread: int, failed: int) -> str:
    parts: list[str] = []
    if failed:
        parts.append(
            f"{failed} document{'' if failed == 1 else 's'} failed model extraction and will be retried"
        )
    if unread:
        parts.append(
            f"{unread} document{'' if unread == 1 else 's'} not read because the scan hit its "
            f"{int(SCAN_DEADLINE)}s budget"
        )
    return "; ".join(parts)


def _component_document(doc: dict) -> KnowledgeDocument:
    return KnowledgeDocument(
        str(doc["id"]), str(doc["title"]), str(doc.get("body") or doc.get("snippet") or ""),
        revision=str(doc.get("updated_src") or ""), metadata={"source": str(doc.get("source") or "")},
    )


def _ground_extraction_payload(value: t.Any, document: KnowledgeDocument,
                               collection: str, text_field: str) -> t.Any:
    """Keep only candidates whose evidence can be proven from this document.

    Small local models often paraphrase the quote even when their claim is an
    exact sentence. In that case the exact claim is the stronger quote. If
    neither string occurs, the candidate is discarded; fabricated evidence is
    never repaired with a fuzzy or model-derived guess.
    """
    if not isinstance(value, dict) or not isinstance(value.get(collection), list):
        return value
    grounded: list[dict] = []
    for raw in value[collection]:
        if not isinstance(raw, dict):
            continue
        exact_text = str(raw.get(text_field) or "").strip()
        evidence: list[dict] = []
        for reference in raw.get("evidence") or ():
            if not isinstance(reference, dict) or str(reference.get("document_id") or "") != document.external_id:
                continue
            quote = str(reference.get("quote") or "").strip()
            if quote and quote in document.body:
                evidence.append({"document_id": document.external_id, "quote": quote})
            elif exact_text and exact_text in document.body:
                evidence.append({"document_id": document.external_id, "quote": exact_text})
        if exact_text and evidence:
            grounded.append({**raw, "evidence": evidence})
    return {collection: grounded}


# ————————————————— LLM helpers for mutations —————————————————

SKILL_PROMPTS = {
    "tighten": "Tighten the prose: remove filler words and redundancy.",
    "plain": "Rewrite in plain language: shorter words, no jargon.",
    "active": "Convert passive voice to active voice.",
    "inclusive": "Replace non-inclusive or ambiguous terms.",
    "terminology": "Align terms with the team glossary.",
    "headings": "Improve headings to be descriptive and parallel.",
    "translate": "Translate the passage to French, keeping technical terms.",
}

def llm_refine(doc: dict, skill: str) -> list[tuple[str, str, str]]:
    document = _component_document(doc)
    edits = component_refine_document(
        document,
        SKILL_PROMPTS.get(skill, SKILL_PROMPTS["tighten"]),
        generate_json=lambda prompt, _version: llm.generate_json(
            prompt, system="You are Mari, a precise technical editor."),
    )
    return [(edit.original[:300], edit.replacement[:300], edit.reason[:120]) for edit in edits]


# ——— LLM scanners: mine the doc graph for candidates ———
#
# The `*_for` functions are the scan; the mutations are thin wrappers that
# supply the default document list. The flow steps call the functions with
# ctx["doc_ids"], which is what makes the flow's fetch_docs step real
# (FACT-4) — before, the step computed a list and the scan ignored it.

def scan_decisions_for(doc_ids: list[int] | None = None,
                       limit: int = SCAN_DOCS) -> tuple[int, int, str]:
    """Mine `doc_ids` (or the least-recently-scanned documents) for
    decisions. Returns (added, documents read, note) — the note is '' when
    the scan finished, and says what was left unread when it did not."""
    docs = _scan_batch("decisions", doc_ids, limit)
    if not docs:
        return 0, 0, ""
    existing = knowledge_store.decision_statements()

    def extract(doc: dict):
        document = _component_document(doc)
        return component_extract_decisions(
            [document],
            generate_json=lambda prompt, _version: _ground_extraction_payload(
                llm.generate_json(
                    prompt, "You mine team knowledge for decisions worth ratifying.", SCAN_CALL_TIMEOUT,
                    schema=_DECISION_SCHEMA),
                document, "decisions", "statement",
            ),
            maximum_documents=1, maximum_characters=1500,
        )

    results, unread, failed = _scan_concurrently(docs, extract)
    if not results and failed:
        raise RuntimeError(f"Model extraction failed for all {failed} completed documents")

    added = 0
    for doc, out in results:
        for item in out[:CLAIMS_PER_DOC]:
            stmt = (item.statement if hasattr(item, "statement")
                    else str(item.get("statement", ""))).strip()[:200]
            if not stmt or stmt.lower() in existing:
                continue
            if knowledge_store.add_decision(
                stmt,
                ((item.evidence[0].quote if item.evidence else "")
                 if hasattr(item, "evidence") else str(item.get("context", "")))[:400],
                ("Mari scan · " + doc["title"])[:120], actor_name(),
            ):
                existing.add(stmt.lower())
                added += 1

    _mark_scanned("decisions", [doc["id"] for doc, _ in results])
    note = _scan_note(unread, failed)
    audit("scanned for decisions", f"{added} candidates from {len(results)} documents"
                                   + (f" ({note})" if note else ""))
    return added, len(results), note

def scan_facts_for(doc_ids: list[int] | None = None,
                   limit: int = SCAN_DOCS) -> tuple[int, int, str]:
    """Mine `doc_ids` (or the least-recently-scanned documents) for atomic,
    checkable claims; they land as 'Needs review' facts.

    One document per model call, not one call over a pasted-together
    corpus. The old shape asked the model which document each claim came
    from and stored the answer as a text label, which meant the provenance
    was the model's recollection of a title — good enough to print, not
    good enough to key on, so Doc Review could never list a document's own
    claims. Reading one document at a time makes `document_id` a fact about
    the call rather than a guess about the output.

    Returns (added, documents read, note)."""
    docs = [d for d in _scan_batch("facts", doc_ids, limit)
            if (d["body"] or d["snippet"] or "").strip()]
    if not docs:
        return 0, 0, ""
    existing = knowledge_store.fact_claims()

    def extract(doc: dict):
        document = _component_document(doc)
        return component_extract_facts(
            [document],
            generate_json=lambda prompt, _version: _ground_extraction_payload(
                llm.generate_json(
                    prompt, "You extract verifiable facts from documentation.", SCAN_CALL_TIMEOUT,
                    schema=_FACT_SCHEMA),
                document, "facts", "claim",
            ),
            maximum_documents=1, maximum_characters=1500,
        )

    results, unread, failed = _scan_concurrently(docs, extract)
    if not results and failed:
        raise RuntimeError(f"Model extraction failed for all {failed} completed documents")

    # The ceiling is per document (FACT-2). A shared budget meant the first
    # document's claims displaced the fifth document's, and the fifth
    # document was read for nothing — silently, since the count it returned
    # looked exactly like a document that had no claims in it.
    added = 0
    for doc, out in results:
        for item in out[:CLAIMS_PER_DOC]:
            claim = (item.claim if hasattr(item, "claim")
                     else str(item.get("claim", ""))).strip()[:200]
            if not is_claim(claim) or claim.lower() in existing:
                continue
            # `source` stays the human label it always was; `document_id`
            # is the key, and it is the document this call actually read.
            if knowledge_store.add_fact(
                claim, ("Mari scan · " + doc["title"])[:80], actor_name(), doc["id"],
            ):
                existing.add(claim.lower())
                added += 1

    _mark_scanned("facts", [doc["id"] for doc, _ in results])
    note = _scan_note(unread, failed)
    audit("scanned for facts", f"{added} candidates from {len(results)} documents"
                               + (f" ({note})" if note else ""))
    return added, len(results), note


def fact_check_document(document_id: int) -> int:
    doc = knowledge_store.document(document_id)
    if not doc:
        return 0
    facts = knowledge_store.fact_claims(verified_only=True)
    assessments = component_check_claims(
        list(facts), [_component_document(doc)],
        generate_json=lambda prompt, _version: llm.generate_json(
            prompt, system="You are Mari, a rigorous fact checker."),
        maximum_documents=1,
    )
    found = 0
    for assessment in assessments[:5]:
        if assessment.verdict != "contradicted" or not assessment.evidence:
            continue
        if knowledge_store.add_finding(
            document_id, assessment.evidence[0].quote[:200],
            f"{assessment.claim}: {assessment.explanation}"[:300],
        ):
            found += 1
    audit("ran fact check", doc["title"])
    return found


def validate_github_pull_request(source: dict, number: int, delivery_id: str = "") -> None:
    """Check a PR against verified workspace facts and post the result.

    The webhook is only a durable hint. This function fetches the canonical PR
    and changed-file patches from GitHub before asking the fact checker, then
    posts one replay-safe inbox result back to the PR conversation.
    """
    if number < 1:
        raise ValueError("GitHub pull request number is required")
    cfg = source.get("config") if isinstance(source.get("config"), dict) else json.loads(source.get("config") or "{}")
    token = str(cfg.get("token") or "").strip()
    repository = str(cfg.get("repo") or "").strip()
    github = GitHubConfig(token, repository)
    from mari_server.providers.connectors import http_transport
    marker = f"<!-- mari-fact-validation:{delivery_id} -->" if delivery_id else ""
    if marker and any(marker in str(comment.get("body") or "")
                      for comment in github_issue_comments(github, number, http=http_transport, limit=100)):
        return
    pull = github_pull_request(github, number, http=http_transport)
    files = github_pull_files(github, number, http=http_transport)
    sections = [str(pull.get("title") or ""), str(pull.get("body") or "")]
    for file in files[:100]:
        filename = str(file.get("filename") or "")
        patch = str(file.get("patch") or "")
        if patch:
            sections.append(f"File: {filename}\n{patch}")
    body = "\n\n".join(section for section in sections if section).strip()
    claims = sorted(knowledge_store.fact_claims(verified_only=True, original_case=True))
    if not claims:
        report = "## Mari fact validation\n\nNo verified workspace facts are available yet, so this pull request could not be validated."
    elif not body:
        report = "## Mari fact validation\n\nThis pull request has no readable description or text patch to validate."
    else:
        document = KnowledgeDocument(
            f"github-pr:{repository}#{number}", f"Pull request #{number}", body,
            revision=str(pull.get("updated_at") or ""),
        )
        assessments = component_check_claims(
            claims, [document],
            generate_json=lambda prompt, _version: llm.generate_json(
                prompt, system="You validate proposed GitHub changes against verified product facts."),
            maximum_claims=50, maximum_documents=1, maximum_characters=60_000,
        )
        contradictions = [item for item in assessments if item.verdict == "contradicted"]
        supported = sum(item.verdict == "supported" for item in assessments)
        uncertain = len(assessments) - supported - len(contradictions)
        lines = [
            "## Mari fact validation", "",
            f"Checked {len(assessments)} verified workspace facts against this pull request: "
            f"**{supported} supported**, **{len(contradictions)} contradicted**, **{uncertain} not addressed**.",
        ]
        if contradictions:
            lines.extend(["", "### Contradictions"])
            for item in contradictions[:10]:
                lines.append(f"- **{item.claim}**: {item.explanation}")
        else:
            lines.extend(["", "No contradictions were found in the text GitHub exposed for this change."])
        lines.extend(["", "_This checks text evidence against verified Mari facts; it is not a code-quality review._"])
        report = "\n".join(lines)
    post_github_comment(
        GitHubCommentTarget(token, repository, number),
        report + (f"\n\n{marker}" if marker else ""), http=http_transport,
    )
    audit("validated GitHub pull request facts", f"{repository}#{number}")


def derive_links() -> int:
    project_id = access.require_current_access().project_id
    added = links.extract_all(project_id)
    audit("derived semantic links", f"{added} new edges")
    return added


def regenerate_digest() -> bool:
    docs = knowledge_store.recent_documents(8)
    documents = [_component_document(doc) for doc in docs]
    result = component_summarize_digest(
        documents,
        generate_json=lambda prompt, _version: llm.generate_json(
            prompt, system="You are Mari, summarizing the team's week."),
        maximum_documents=8,
    )
    by_id = {document.external_id: row for document, row in zip(documents, docs)}
    topics = []
    for topic in result.topics[:3]:
        wheres, seen = [], set()
        for evidence in topic.evidence:
            row = by_id[evidence.document_id]
            key = (row["source"], row["title"])
            if key not in seen:
                seen.add(key)
                wheres.append({"source": row["source"], "label": row["title"]})
        topics.append((topic.title[:120], topic.summary[:500], json.dumps(wheres), "[]"))

    knowledge_store.replace_digest(topics)
    audit("regenerated digest", f"{len(topics)} topics")
    return True

"""Mari — knowledge mutations: docs, facts, decisions, answers, glossary,
tags, tasks, lineage, digest, insights, watches."""

from __future__ import annotations

import concurrent.futures as cf
import contextvars
import datetime as dt
import hashlib
import json
import time
import typing as t

from mari_server.automations import progress as step_progress
from mari_server.providers import models as llm
from mari_server.identity import context as access
from mari_server.persistence.postgres import lineage as links
from mari_server.persistence.postgres import knowledge as knowledge_store
from mari_server.persistence.postgres import fact_intelligence as fact_store
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
FACT_REPRESENTATION_PROFILE = "fact-components-v1"

_EVIDENCE_SCHEMA = {
    "type": "array", "items": {"type": "object", "properties": {
        "document_id": {"type": "string"}, "quote": {"type": "string"},
    }, "required": ["document_id", "quote"]},
}
_FACT_SCHEMA = {"type": "object", "properties": {"facts": {
    "type": "array", "items": {"type": "object", "properties": {
        "claim": {"type": "string"},
        "atomic_claims": {"type": "array", "items": {"type": "string"}},
        "subject": {"type": "object", "properties": {
            "canonical": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
        }, "required": ["canonical", "aliases"]},
        "relation": {"type": "string"},
        "object": {"type": "string"},
        "scopes": {"type": "array", "items": {"type": "string"}},
        "valid_from": {"type": ["string", "null"]},
        "valid_to": {"type": ["string", "null"]},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "evidence": _EVIDENCE_SCHEMA,
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
) -> tuple[list[tuple[dict, t.Any]], int, int, list[str]]:
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
    errors: list[str] = []
    pool = cf.ThreadPoolExecutor(max_workers=min(SCAN_WORKERS, len(docs)),
                                 thread_name_prefix="mari-scan")
    futures = {
        pool.submit(contextvars.copy_context().run, operation, document): document
        for document in docs
    }
    # Collected as they land, not in one wait: each completion reports
    # within-step progress, so a run's bar moves through the model pass
    # instead of parking at the step boundary for the whole scan.
    finished: set = set()
    try:
        for future in cf.as_completed(futures, timeout=SCAN_DEADLINE):
            finished.add(future)
            document = futures[future]
            try:
                results.append((document, future.result()))
            except Exception as error:  # noqa: BLE001 — one bad document must not end the scan
                failed += 1
                detail = str(error).strip()
                errors.append(f"{type(error).__name__}: {detail}" if detail else type(error).__name__)
            step_progress.report(len(finished), len(docs))
    except cf.TimeoutError:
        pass
    pending = [future for future in futures if future not in finished]
    for future in pending:
        future.cancel()
    pool.shutdown(wait=False, cancel_futures=True)
    return results, len(pending), failed, errors


def _extraction_json(prompt: str, system: str, schema: dict[str, t.Any],
                     max_tokens: int | None = None) -> t.Any:
    """Generate extraction JSON without losing the provider's real failure."""
    value = llm.generate_json(
        prompt, system, SCAN_CALL_TIMEOUT, schema=schema, max_tokens=max_tokens,
    )
    if value is None:
        raise RuntimeError(llm.last_error() or "the model returned no structured output")
    return value


def _all_failed(kind: str, failed: int, errors: list[str]) -> RuntimeError:
    first = errors[0] if errors else "unknown model error"
    suffix = f"; first error: {first}"
    return RuntimeError(
        f"Model extraction failed for all {failed} completed {kind}{suffix}"
    )


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
                _extraction_json(
                    prompt, "You mine team knowledge for decisions worth ratifying.", _DECISION_SCHEMA),
                document, "decisions", "statement",
            ),
            maximum_documents=1, maximum_characters=1500,
        )

    results, unread, failed, errors = _scan_concurrently(docs, extract)
    if not results and failed:
        raise _all_failed("documents", failed, errors)

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

def extract_fact_candidates_for(doc_ids: list[int] | None = None,
                                limit: int = SCAN_DOCS,
                                claims_per_document: int = CLAIMS_PER_DOC,
                                instructions: str = "", *, run_id: int | None = None,
                                max_llm_calls: int | None = None,
                                max_input_tokens: int = 100_000,
                                max_output_tokens: int = 20_000) -> tuple[list[dict], int, str]:
    """Mine documents into evidence-bearing candidates without publishing.

    One document per model call, not one call over a pasted-together
    corpus. The old shape asked the model which document each claim came
    from and stored the answer as a text label, which meant the provenance
    was the model's recollection of a title — good enough to print, not
    good enough to key on, so Doc Review could never list a document's own
    claims. Reading one document at a time makes `document_id` a fact about
    the call rather than a guess about the output.

    Returns (candidates, documents read, note)."""
    docs = [d for d in _scan_batch("facts", doc_ids, limit)
            if (d["body"] or d["snippet"] or "").strip()]
    if not docs:
        return 0, 0, ""
    extraction_purpose = "structured fact extraction"
    call_limit = max(0, min(int(max_llm_calls if max_llm_calls is not None else len(docs)), 200))
    output_per_call = max(200, min(2000, max_output_tokens // max(1, call_limit)))
    if run_id is not None:
        provider, model = llm.generation_model()
        fact_store.configure_llm_budget(
            run_id, stage="scan_facts", purpose=extraction_purpose,
            provider=provider, model=model, recipe="facts-extract-v3",
            max_calls=call_limit, max_input_tokens=max(0, min(max_input_tokens, 2_000_000)),
            max_output_tokens=max(0, min(max_output_tokens, 400_000)),
            visible_config={
                "documents_selected": len(docs), "maximum_calls": call_limit,
                "claims_per_document": claims_per_document,
                "output_tokens_per_call": output_per_call,
                "instructions": instructions[:1000],
            },
        )
    budget_omitted = max(0, len(docs) - call_limit)
    docs = docs[:call_limit]
    if not docs:
        if run_id is not None:
            fact_store.complete_llm_budget(
                run_id, stage="scan_facts", purpose=extraction_purpose, status="skipped",
            )
        return [], 0, f"{budget_omitted} documents not read because the configured LLM call budget is zero"
    existing = knowledge_store.fact_claims()

    def extract(doc: dict):
        if run_id is not None and not fact_store.reserve_llm_call(
            run_id, stage="scan_facts", purpose=extraction_purpose,
            estimated_input_tokens=max(1, len(str(doc.get("body") or doc.get("snippet") or "")) // 3 + 300),
            output_tokens=output_per_call,
        ):
            return ()
        document = _component_document(doc)
        return component_extract_facts(
            [document],
            generate_json=lambda prompt, _version: _ground_extraction_payload(
                _extraction_json(
                    prompt, "You extract verifiable facts from documentation."
                    + (f" Reviewer instructions: {instructions[:1000]}" if instructions else ""),
                    _FACT_SCHEMA, output_per_call),
                document, "facts", "claim",
            ),
            maximum_documents=1, maximum_characters=1500,
        )

    results, unread, failed, errors = _scan_concurrently(docs, extract)
    if not results and failed:
        raise _all_failed("documents", failed, errors)

    # The ceiling is per document (FACT-2). A shared budget meant the first
    # document's claims displaced the fifth document's, and the fifth
    # document was read for nothing — silently, since the count it returned
    # looked exactly like a document that had no claims in it.
    candidates: list[dict] = []
    seen = set(existing)
    for doc, out in results:
        for item in out[:max(1, min(int(claims_per_document), 10))]:
            claim = (item.claim if hasattr(item, "claim")
                     else str(item.get("claim", ""))).strip()[:200]
            if not is_claim(claim) or claim.lower() in seen:
                continue
            evidence = ""
            confidence = 0.0
            if hasattr(item, "evidence"):
                evidence = item.evidence[0].quote if item.evidence else ""
                confidence = float(getattr(item, "confidence", 0) or 0)
            elif isinstance(item, dict):
                raw_evidence = item.get("evidence") or []
                evidence = str((raw_evidence[0] if raw_evidence else {}).get("quote") or "")
                confidence = float(item.get("confidence") or 0)
            candidates.append({
                "claim": claim,
                "source": ("Mari scan · " + doc["title"])[:80],
                "document_id": doc["id"],
                "evidence": evidence[:1000],
                "confidence": confidence,
                "structured_claim": dict(getattr(item, "qualifiers", {}) or {}),
                "extraction_recipe": "facts-extract-v3",
            })
            seen.add(claim.lower())

    _mark_scanned("facts", [doc["id"] for doc, _ in results])
    note = _scan_note(unread, failed)
    if budget_omitted:
        note = "; ".join(part for part in (
            note,
            f"{budget_omitted} document{'s' if budget_omitted != 1 else ''} deferred by the configured LLM call budget",
        ) if part)
    if run_id is not None:
        fact_store.complete_llm_budget(
            run_id, stage="scan_facts", purpose=extraction_purpose, status="completed",
        )
    audit("scanned for facts", f"{len(candidates)} candidates from {len(results)} documents"
                               + (f" ({note})" if note else ""))
    return candidates, len(results), note


def scan_facts_for(doc_ids: list[int] | None = None,
                   limit: int = SCAN_DOCS,
                   claims_per_document: int = CLAIMS_PER_DOC) -> tuple[int, int, str]:
    """Legacy immediate scan. Workflow runs use staged candidates instead."""
    candidates, scanned, note = extract_fact_candidates_for(
        doc_ids, limit=limit, claims_per_document=claims_per_document,
    )
    added = 0
    for candidate in candidates:
        if knowledge_store.add_fact(
            candidate["claim"], candidate["source"], actor_name(), candidate["document_id"],
        ):
            added += 1
    return added, scanned, note


def ai_review_fact_candidates(run_id: int, instructions: str = "") -> dict[str, int]:
    """Ground every staged candidate against its source and persist a verdict."""
    candidates = knowledge_store.fact_candidates(run_id)
    reviewer = f"AI · {llm.model_identity()}" if hasattr(llm, "model_identity") else "AI reviewer"
    for candidate in candidates:
        if candidate["review_status"] != "pending":
            continue
        document_ids: list[int] = []
        if candidate.get("document_id"):
            document_ids.append(int(candidate["document_id"]))
        for link in knowledge_store.semantic_links("candidate", candidate["id"]):
            if link["target_type"] == "document" and int(link["target_id"]) not in document_ids:
                document_ids.append(int(link["target_id"]))
        docs = [doc for document_id in document_ids[:7]
                if (doc := knowledge_store.document(document_id))]
        if not docs:
            knowledge_store.review_fact_candidate(
                candidate["id"], accepted=False, reviewer=reviewer,
                reason="Source document is no longer available.", kind="ai",
            )
            continue
        system = (
            "You are Mari, a rigorous temporal fact reviewer. Accept only claims directly supported "
            "by the supplied evidence neighborhood. Document revisions are dates: when the business "
            "has evolved, newer authoritative evidence may supersede older statements. Reject a claim "
            "when newer evidence contradicts it; explain the temporal conflict."
        )
        if instructions:
            system += f" Review policy: {instructions[:1000]}"
        assessments = component_check_claims(
            [candidate["claim"]], [_component_document(doc) for doc in docs],
            generate_json=lambda prompt, _version: llm.generate_json(prompt, system=system),
            maximum_claims=1, maximum_documents=len(docs), maximum_characters=30_000,
        )
        assessment = assessments[0]
        knowledge_store.review_fact_candidate(
            candidate["id"], accepted=assessment.verdict == "supported", reviewer=reviewer,
            reason=assessment.explanation, kind="ai",
        )
    return knowledge_store.fact_candidate_counts(run_id)


def _temporal_value(value: t.Any) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def _fact_component_texts(claim: str, structured: dict, *, limit: int) -> list[dict[str, str]]:
    """Render inspectable semantic components for ordinary provider embeddings."""
    output: list[dict[str, str]] = [{"role": "claim", "text": claim.strip()}]

    def add(role: str, text: t.Any) -> None:
        value = str(text or "").strip()
        if value:
            output.append({"role": role, "text": value})

    for atomic in structured.get("atomic_claims") or ():
        add("atomic_claim", atomic)
    subject = structured.get("subject")
    if isinstance(subject, dict):
        canonical = str(subject.get("canonical") or "").strip()
        aliases = [str(value).strip() for value in subject.get("aliases") or () if str(value).strip()]
        add("subject", canonical + (f" (also: {', '.join(aliases)})" if aliases else ""))
    else:
        add("subject", subject)
    add("relation", structured.get("relation"))
    add("object", structured.get("object"))
    for scope in structured.get("scopes") or ():
        add("scope", scope)
    valid_from, valid_to = structured.get("valid_from"), structured.get("valid_to")
    if valid_from or valid_to:
        add("time", f"Valid from {valid_from or 'unknown'} to {valid_to or 'open'}")
    for condition in structured.get("conditions") or ():
        add("condition", condition)

    deduplicated: list[dict[str, str]] = []
    seen: set[str] = set()
    for component in output:
        key = component["text"].casefold()
        if key and key not in seen:
            seen.add(key)
            deduplicated.append(component)
    return deduplicated[:max(1, limit)]


def build_fact_representations(run_id: int, *, max_components: int = 12) -> dict[str, int]:
    """Persist reconstructible embedding sets for active and proposed assertions."""
    provider, model = llm.embedding_model()
    profile = llm.embedding_profile()
    generation_provider, generation_model = llm.generation_model()
    fact_store.ensure_run_assertions(
        run_id, model=f"{generation_provider}:{generation_model}".strip(":"),
    )
    subjects = fact_store.representation_subjects(run_id)
    stored = fact_store.component_hashes(profile, FACT_REPRESENTATION_PROFILE)
    embedded_assertions = 0
    embedded_components = 0
    for subject in subjects:
        structured = subject.get("structured_claim") or {}
        if subject.get("candidate_id"):
            fact_store.update_assertion_structure(
                int(subject["assertion_id"]), structured,
                valid_from=_temporal_value(structured.get("valid_from")),
                valid_to=_temporal_value(structured.get("valid_to")),
            )
        components = _fact_component_texts(
            str(subject["claim"]), structured, limit=max(1, min(max_components, 32)),
        )
        combined = ":".join(hashlib.sha256(item["text"].encode()).hexdigest()
                            for item in components)
        if stored.get(int(subject["assertion_id"])) == combined:
            continue
        vectors = llm.embed_many([item["text"] for item in components], purpose="document")
        if len(vectors) != len(components) or any(vector is None for vector in vectors):
            raise RuntimeError(
                f"Fact component embedding failed: {llm.last_error() or 'provider returned no vector'}"
            )
        fact_store.replace_components(
            int(subject["assertion_id"]), embedding_profile=profile,
            representation_profile=FACT_REPRESENTATION_PROFILE,
            provider=provider, model=model,
            components=[{**item, "embedding": vector}
                        for item, vector in zip(components, vectors, strict=True)],
        )
        embedded_assertions += 1
        embedded_components += len(components)
    return {"embedded_assertions": embedded_assertions,
            "embedded_components": embedded_components}


def map_fact_candidate_impact(run_id: int, *, retrieval_backend: str = "postgres",
                              fact_neighbors: int = 8, evidence_neighbors: int = 8,
                              minimum_fact_similarity: float = .72,
                              minimum_evidence_similarity: float = .68,
                              max_components: int = 12) -> dict[str, int]:
    """Embed candidates and map their temporal fact/document neighborhood.

    Fact-to-document lookup deliberately uses chunk MaxSim in persistence: a
    supporting passage should not disappear inside an unrelated document
    centroid. Links snapshot source timestamps and hashes so an invalidation
    report remains explainable after the corpus evolves.
    """
    if retrieval_backend != "postgres":
        raise ValueError("The active fact retrieval backend must be postgres")
    representation_stats = build_fact_representations(
        run_id, max_components=max_components,
    )
    profile = llm.embedding_profile()
    assertions = {int(row["candidate_id"]): row
                  for row in fact_store.representation_subjects(run_id)
                  if row.get("candidate_id")}
    candidates = knowledge_store.fact_candidates(run_id)
    high_impact = 0
    linked = 0
    # Import at execution time: product queries owns the existing conservative
    # numeric/polarity detector, and importing it at module load would create a
    # GraphQL service cycle.
    from mari_server.product.queries import detect_contradictions
    for candidate in candidates:
        assertion = assertions.get(int(candidate["id"]))
        if not assertion:
            continue
        nearby_facts = fact_store.assertion_neighbors(
            int(assertion["assertion_id"]), profile, FACT_REPRESENTATION_PROFILE,
            limit=max(1, min(fact_neighbors, 50)),
            minimum_similarity=max(-1.0, min(minimum_fact_similarity, 1.0)),
        )
        nearby_evidence = fact_store.evidence_neighbors(
            int(assertion["assertion_id"]), profile, FACT_REPRESENTATION_PROFILE,
            limit=max(1, min(evidence_neighbors, 50)),
            minimum_similarity=max(-1.0, min(minimum_evidence_similarity, 1.0)),
            exclude_document_id=candidate.get("document_id"),
        )
        fact_store.replace_embedding_relations(
            int(assertion["assertion_id"]), nearby_facts,
            retrieval_profile=f"{retrieval_backend}:{profile}:{FACT_REPRESENTATION_PROFILE}",
        )
        fact_store.replace_embedding_evidence(
            int(assertion["assertion_id"]), nearby_evidence,
            retrieval_profile=f"{retrieval_backend}:{profile}:{FACT_REPRESENTATION_PROFILE}",
        )
        links: list[dict] = []
        contradictions = 0
        if candidate.get("document_id"):
            source = knowledge_store.document(candidate["document_id"])
            if source:
                links.append({
                    "target_type": "document", "target_id": source["id"], "similarity": 1.0,
                    "relation": "source", "target_label": source["title"],
                    "target_updated_at": source.get("updated_src"),
                    "target_content_hash": source.get("content_hash") or "",
                })
        for neighbor in nearby_facts:
            relation = "related"
            if detect_contradictions([{"claim": candidate["claim"]}, {"claim": neighbor["claim"]}]):
                relation = "contradicts"
                contradictions += 1
            links.append({
                "target_type": "fact", "target_id": neighbor["fact_id"],
                "similarity": float(neighbor["similarity"]), "relation": relation,
                "target_label": neighbor["claim"],
                "target_updated_at": neighbor.get("recorded_from"),
                "target_content_hash": "",
            })
        for neighbor in nearby_evidence:
            links.append({
                "target_type": "document", "target_id": neighbor["document_id"],
                "similarity": float(neighbor["similarity"]), "relation": "related",
                "target_label": neighbor["title"],
                "target_updated_at": neighbor.get("updated_src"),
                "target_content_hash": neighbor.get("content_hash") or "",
            })
        score = len(nearby_facts) * 2 + len(nearby_evidence) + contradictions * 5
        high = contradictions > 0 or score >= 6
        knowledge_store.replace_candidate_semantic_links(
            candidate["id"], profile, links, impact_score=score, high_impact=high,
        )
        linked += len(links)
        high_impact += int(high)
    return {"impact_links": linked, "high_impact_facts": high_impact,
            **representation_stats}


_ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string", "enum": [
            "new_fact", "supersede", "qualify", "duplicate", "reject", "needs_review",
        ]},
        "relation": {"type": "string", "enum": [
            "supports", "contradicts", "supersedes", "qualifies", "duplicate",
            "related", "insufficient",
        ]},
        "target_assertion_id": {"type": ["integer", "null"]},
        "valid_from": {"type": ["string", "null"]},
        "valid_to": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "needs_human_review": {"type": "boolean"},
        "evidence_groups": {"type": "array", "items": {
            "type": "object", "properties": {
                "span_ids": {"type": "array", "items": {"type": "integer"}},
                "verdict": {"type": "string", "enum": [
                    "supports", "contradicts", "qualifies", "insufficient",
                ]},
                "sufficient": {"type": "boolean"},
                "confidence": {"type": "number"},
                "explanation": {"type": "string"},
            }, "required": ["span_ids", "verdict", "sufficient", "confidence", "explanation"],
        }},
    },
    "required": ["recommendation", "relation", "target_assertion_id", "valid_from",
                 "valid_to", "confidence", "reason", "needs_human_review", "evidence_groups"],
}


def _jsonable_packet(packet: dict, *, relation_limit: int, evidence_limit: int) -> dict:
    assertion = packet["assertion"]
    return {
        "assertion": {
            "id": assertion["id"], "claim": assertion["claim"],
            "structured_claim": assertion.get("structured_claim") or {},
            "valid_from": str(assertion.get("valid_from") or ""),
            "valid_to": str(assertion.get("valid_to") or ""),
        },
        "related_assertions": [{
            "assertion_id": row["target_assertion_id"], "claim": row["claim"],
            "structured_claim": row.get("structured_claim") or {},
            "valid_from": str(row.get("valid_from") or ""),
            "valid_to": str(row.get("valid_to") or ""),
            "recorded_from": str(row.get("recorded_from") or ""),
            "similarity": float(row.get("exact_score") or 0),
            "criticality": row.get("criticality") or "normal",
        } for row in packet["relations"][:relation_limit]],
        "evidence": [{
            "span_id": row["span_id"], "document_id": row["document_id"],
            "title": row["title"], "source": row["source"], "quote": row["quote"],
            "source_authority": row["source_authority"],
            "published_at": str(row.get("published_at") or ""),
            "effective_from": str(row.get("effective_from") or ""),
            "effective_to": str(row.get("effective_to") or ""),
            "revised_at": str(row.get("revised_at") or ""),
            "ingested_at": str(row.get("ingested_at") or ""),
            "similarity": float(row.get("similarity") or 0),
        } for row in packet["evidence"][:evidence_limit]],
    }


def adjudicate_fact_candidates(run_id: int, *, enabled: bool, max_calls: int = 10,
                               max_input_tokens: int = 24_000,
                               max_output_tokens: int = 8_000,
                               output_tokens_per_call: int = 800,
                               related_assertions: int = 8, evidence_spans: int = 12,
                               instructions: str = "") -> dict[str, int]:
    """Optionally ask the configured LLM to adjudicate embedding candidates.

    The durable budget row is created before any call and every call reserves
    its visible allowance atomically. Exhaustion is a review outcome, not an
    unbounded retry condition.
    """
    provider, model = llm.generation_model()
    purpose = "temporal evidence and relation proposals"
    recipe = "fact-adjudication-v1"
    fact_store.configure_llm_budget(
        run_id, stage="adjudicate_facts", purpose=purpose, provider=provider,
        model=model, recipe=recipe, max_calls=max(0, min(max_calls, 100)),
        max_input_tokens=max(0, min(max_input_tokens, 1_000_000)),
        max_output_tokens=max(0, min(max_output_tokens, 200_000)),
        visible_config={
            "enabled": enabled, "related_assertions": related_assertions,
            "evidence_spans": evidence_spans,
            "output_tokens_per_call": output_tokens_per_call,
            "instructions": instructions[:1000],
        },
    )
    if not enabled:
        fact_store.complete_llm_budget(
            run_id, stage="adjudicate_facts", purpose=purpose, status="skipped",
        )
        return {"llm_calls": 0, "adjudicated_facts": 0, "llm_abstentions": 0,
                "llm_budget_exhausted": 0}

    adjudicated = abstentions = calls = exhausted = 0
    retrieval_profile = (
        f"postgres:{llm.embedding_profile()}:{FACT_REPRESENTATION_PROFILE}"
    )
    for assertion_id in fact_store.run_assertion_ids(run_id):
        packet = fact_store.adjudication_packet(assertion_id)
        if not packet:
            continue
        visible = _jsonable_packet(
            packet, relation_limit=max(1, min(related_assertions, 20)),
            evidence_limit=max(1, min(evidence_spans, 30)),
        )
        prompt = json.dumps(visible, sort_keys=True, default=str)
        estimated_input = max(1, len(prompt) // 3)
        per_call = max(100, min(output_tokens_per_call, 4000))
        if not fact_store.reserve_llm_call(
            run_id, stage="adjudicate_facts", purpose=purpose,
            estimated_input_tokens=estimated_input, output_tokens=per_call,
        ):
            exhausted = 1
            break
        calls += 1
        system = (
            "You adjudicate evolving business facts using only the supplied assertion, related "
            "assertions, and evidence spans. Similarity is discovery context, not proof. Distinguish "
            "business scope and effective time. A newer source does not automatically override a more "
            "authoritative source. Return insufficient or needs_review when evidence is incomplete. "
            "Never cite a span or assertion id absent from the packet."
        )
        if instructions:
            system += f" Workspace review policy: {instructions[:1000]}"
        result = llm.generate_json(
            prompt, system=system, timeout=90, schema=_ADJUDICATION_SCHEMA,
            max_tokens=per_call,
        )
        if not isinstance(result, dict):
            abstentions += 1
            continue
        allowed_targets = {int(row["target_assertion_id"]) for row in packet["relations"]}
        target_id = int(result.get("target_assertion_id") or 0)
        if target_id not in allowed_targets:
            result["target_assertion_id"] = None
            if result.get("relation") not in {"supports", "insufficient"}:
                result["relation"] = "insufficient"
        allowed_spans = {int(row["span_id"]) for row in packet["evidence"]}
        for group in result.get("evidence_groups") or ():
            if isinstance(group, dict):
                group["span_ids"] = [int(value) for value in group.get("span_ids") or ()
                                     if str(value).isdigit() and int(value) in allowed_spans]
        result["valid_from"] = _temporal_value(result.get("valid_from"))
        result["valid_to"] = _temporal_value(result.get("valid_to"))
        context_hash = hashlib.sha256(prompt.encode()).hexdigest()
        fact_store.save_adjudication(
            assertion_id, result, model=f"{provider}:{model}".strip(":"), recipe=recipe,
            context_hash=context_hash, retrieval_profile=retrieval_profile,
        )
        adjudicated += 1
        abstentions += int(result.get("relation") == "insufficient")
    fact_store.complete_llm_budget(
        run_id, stage="adjudicate_facts", purpose=purpose,
        status="exhausted" if exhausted else "completed",
    )
    return {"llm_calls": calls, "adjudicated_facts": adjudicated,
            "llm_abstentions": abstentions, "llm_budget_exhausted": exhausted}


def build_fact_clusters(run_id: int, *, minimum_similarity: float = .78,
                        label_mode: str = "off", max_llm_clusters: int = 5,
                        max_input_tokens: int = 8_000,
                        max_output_tokens: int = 2_000,
                        instructions: str = "") -> dict[str, int]:
    """Build embedding-driven connected neighborhoods and optionally label them."""
    if label_mode not in {"off", "llm"}:
        raise ValueError("Fact cluster label mode must be off or llm")
    nodes, edges = fact_store.cluster_graph(
        run_id, minimum_similarity=max(-1.0, min(minimum_similarity, 1.0)),
    )
    by_id = {int(row["id"]): row for row in nodes}
    parent = {node_id: node_id for node_id in by_id}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    scores: dict[tuple[int, int], float] = {}
    for edge in edges:
        source, target = int(edge["source"]), int(edge["target"])
        if source not in parent or target not in parent:
            continue
        left, right = find(source), find(target)
        if left != right:
            parent[right] = left
        scores[(source, target)] = float(edge["score"])
    groups: dict[int, list[dict]] = {}
    for node_id, node in by_id.items():
        groups.setdefault(find(node_id), []).append(node)

    provider, model = llm.generation_model()
    purpose = "fact cluster labels"
    fact_store.configure_llm_budget(
        run_id, stage="cluster_facts", purpose=purpose, provider=provider, model=model,
        recipe="fact-cluster-label-v1", max_calls=max_llm_clusters if label_mode == "llm" else 0,
        max_input_tokens=max_input_tokens if label_mode == "llm" else 0,
        max_output_tokens=max_output_tokens if label_mode == "llm" else 0,
        visible_config={"mode": label_mode, "maximum_clusters": max_llm_clusters,
                        "minimum_similarity": minimum_similarity,
                        "instructions": instructions[:1000]},
    )
    clusters: list[dict] = []
    calls = exhausted = 0
    for members in sorted(groups.values(), key=lambda rows: (-len(rows), min(int(r["id"]) for r in rows))):
        member_ids = sorted(int(row["id"]) for row in members)
        fact_ids = sorted(int(row["fact_id"]) for row in members if row.get("fact_id"))
        stable_key = f"fact:{fact_ids[0]}" if fact_ids else (
            "assertions:" + hashlib.sha256(
                "|".join(sorted(str(row["claim"]) for row in members)).encode()
            ).hexdigest()[:20]
        )
        label = str(members[0]["claim"])[:120]
        summary = f"{len(members)} semantically related assertion{'s' if len(members) != 1 else ''}."
        label_kind = "none"
        if label_mode == "llm" and calls < max_llm_clusters:
            prompt = json.dumps({"claims": [row["claim"] for row in members[:20]]})
            output_budget = max(100, min(400, max_output_tokens))
            if fact_store.reserve_llm_call(
                run_id, stage="cluster_facts", purpose=purpose,
                estimated_input_tokens=max(1, len(prompt) // 3), output_tokens=output_budget,
            ):
                calls += 1
                result = llm.generate_json(
                    prompt,
                    system=("Label this embedding-derived business fact cluster in at most eight words "
                            "and summarize its shared subject without deciding which claim is true. "
                            + instructions[:1000]),
                    timeout=60,
                    schema={"type": "object", "properties": {
                        "label": {"type": "string"}, "summary": {"type": "string"},
                    }, "required": ["label", "summary"]},
                    max_tokens=output_budget,
                )
                if isinstance(result, dict):
                    label = str(result.get("label") or label)[:120]
                    summary = str(result.get("summary") or summary)[:1000]
                    label_kind = "llm"
            else:
                exhausted = 1
        clusters.append({
            "stable_key": stable_key, "label": label, "summary": summary,
            "label_kind": label_kind,
            "label_model": f"{provider}:{model}".strip(":") if label_kind == "llm" else "",
            "members": [{
                "assertion_id": row["id"],
                "score": max([score for (source, target), score in scores.items()
                              if int(row["id"]) in {source, target}] or [1.0]),
                "explanation": "Embedding neighborhood",
            } for row in members],
        })
    generation = hashlib.sha256(
        json.dumps([[row["stable_key"], [m["assertion_id"] for m in row["members"]]]
                    for row in clusters], sort_keys=True).encode()
    ).hexdigest()[:32]
    fact_store.replace_clusters(
        run_id, clusters, embedding_profile=llm.embedding_profile(),
        retrieval_profile=f"postgres:{llm.embedding_profile()}:{FACT_REPRESENTATION_PROFILE}",
        generation=generation,
    )
    fact_store.complete_llm_budget(
        run_id, stage="cluster_facts", purpose=purpose,
        status="exhausted" if exhausted else ("completed" if label_mode == "llm" else "skipped"),
    )
    return {"fact_clusters": len(clusters), "cluster_llm_calls": calls,
            "cluster_llm_budget_exhausted": exhausted}


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

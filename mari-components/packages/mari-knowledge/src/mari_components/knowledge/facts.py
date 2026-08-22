"""Fact extraction and evidence-based claim assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from mari_components.errors import MalformedModelOutput
from mari_components.types import Evidence, FactCandidate, KnowledgeDocument
from .prompting import JsonGenerator, bounded_documents, documents_json, require_list
from .scoring import evidence_confidence


FACT_EXTRACTION_VERSION = "facts-extract-v2"
FACT_CHECK_VERSION = "facts-check-v2"


def _evidence(values: Any, allowed: Mapping[str, KnowledgeDocument], *, recipe: str) -> tuple[Evidence, ...]:
    if not isinstance(values, list) or not values:
        raise MalformedModelOutput(f"{recipe} evidence must be a non-empty array")
    output: list[Evidence] = []
    for value in values:
        if isinstance(value, str):
            # Smaller models return bare quotes. Accept one when it can be
            # pinned to exactly one allowed document; anything looser is still
            # invented evidence and fails below.
            quote = value.strip()
            holders = [doc_id for doc_id, doc in allowed.items() if quote and quote in doc.body]
            value = {"document_id": holders[0], "quote": quote} if len(holders) == 1 else {"quote": quote}
        if not isinstance(value, dict):
            raise MalformedModelOutput(f"{recipe} evidence entries must be objects")
        document_id = str(value.get("document_id") or "")
        quote = str(value.get("quote") or "").strip()
        if document_id not in allowed and quote and len(allowed) == 1 and quote in next(iter(allowed.values())).body:
            document_id = next(iter(allowed))
        if document_id not in allowed:
            raise MalformedModelOutput(f"{recipe} references an unknown document")
        if not quote:
            raise MalformedModelOutput(f"{recipe} evidence quote is required")
        if quote not in allowed[document_id].body:
            raise MalformedModelOutput(f"{recipe} evidence quote is not present in the document")
        output.append(Evidence(document_id, allowed[document_id].revision, quote))
    return tuple(output)


def extract_facts(
    documents: Iterable[KnowledgeDocument], *, generate_json: JsonGenerator,
    maximum_documents: int = 50, maximum_characters: int = 60_000,
) -> tuple[FactCandidate, ...]:
    bounded = bounded_documents(documents, maximum_documents=maximum_documents, maximum_characters=maximum_characters)
    allowed = {document.external_id: document for document in bounded}
    prompt = (
        "Extract durable, atomic product facts from the supplied documents. "
        "Do not infer beyond the text. Every fact must cite at least one exact document id and quote. "
        'Return JSON {"facts":[{"claim":"...",'
        '"evidence":[{"document_id":"...","quote":"..."}]}]}.\nDocuments:\n'
        + documents_json(bounded)
    )
    rows = require_list(generate_json(prompt, FACT_EXTRACTION_VERSION), "facts", recipe=FACT_EXTRACTION_VERSION)
    output: list[FactCandidate] = []
    for row in rows:
        claim = str(row.get("claim") or "").strip()
        if not claim:
            raise MalformedModelOutput("fact claim is required")
        evidence = _evidence(row.get("evidence"), allowed, recipe=FACT_EXTRACTION_VERSION)
        output.append(FactCandidate(claim, evidence, evidence_confidence(claim, evidence)))
    return tuple(output)


@dataclass(frozen=True, slots=True)
class FactAssessment:
    claim: str
    verdict: str
    explanation: str
    confidence: float
    evidence: tuple[Evidence, ...]
    prompt_version: str = FACT_CHECK_VERSION


def check_claims(
    claims: Iterable[str], documents: Iterable[KnowledgeDocument], *, generate_json: JsonGenerator,
    maximum_claims: int = 50, maximum_documents: int = 50, maximum_characters: int = 60_000,
) -> tuple[FactAssessment, ...]:
    selected_claims = tuple(str(claim).strip() for claim in claims if str(claim).strip())[:maximum_claims]
    bounded = bounded_documents(documents, maximum_documents=maximum_documents, maximum_characters=maximum_characters)
    allowed = {document.external_id: document for document in bounded}
    prompt = (
        "Assess each supplied claim only against the evidence. Verdict must be supported, contradicted, or uncertain. "
        "Preserve every claim exactly and cite evidence for supported or contradicted results. "
        'Return JSON {"assessments":[{"claim":"...","verdict":"supported|contradicted|uncertain",'
        '"explanation":"...","evidence":[...]}]}.\nClaims:\n'
        + "\n".join(f"- {claim}" for claim in selected_claims)
        + "\nDocuments:\n" + documents_json(bounded)
    )
    rows = require_list(generate_json(prompt, FACT_CHECK_VERSION), "assessments", recipe=FACT_CHECK_VERSION)
    matched = _match_assessments(selected_claims, rows)
    if selected_claims and not any(row is not None for row in matched.values()):
        raise MalformedModelOutput("fact check did not address any input claim")
    output: list[FactAssessment] = []
    for claim in selected_claims:
        row = matched[claim]
        if row is None:
            output.append(FactAssessment(claim, "uncertain", "The model did not address this claim.", 0.0, ()))
            continue
        verdict = str(row.get("verdict") or "")
        if verdict not in {"supported", "contradicted", "uncertain"}:
            raise MalformedModelOutput("fact verdict is invalid")
        explanation = str(row.get("explanation") or "")
        if verdict == "uncertain" and not row.get("evidence"):
            evidence: tuple[Evidence, ...] = ()
        else:
            try:
                evidence = _evidence(row.get("evidence"), allowed, recipe=FACT_CHECK_VERSION)
            except MalformedModelOutput:
                # A verdict whose evidence cannot be traced to the documents is
                # not a finding. Keep the claim in the report as unassessed
                # instead of discarding every other verdict in the batch.
                verdict, evidence = "uncertain", ()
                explanation = (explanation + " " if explanation else "") + "(The cited evidence could not be verified against the document.)"
        output.append(FactAssessment(
            claim, verdict, explanation,
            evidence_confidence(claim, evidence), evidence,
        ))
    return tuple(output)


def _normalise_claim(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.casefold()).split())


def _match_assessments(claims: tuple[str, ...], rows: list[dict]) -> dict[str, dict | None]:
    """Pair each input claim with the model's row for it.

    Models paraphrase, reorder, and drop claims. The verdicts are still usable
    when the claim can be recognised, so match exact text first, then a
    normalised form, then a close fuzzy match among the rows not yet taken.
    A claim no row matches stays unassessed rather than failing the whole check.
    """
    from difflib import SequenceMatcher

    remaining = [row for row in rows if isinstance(row, dict)]
    matched: dict[str, dict | None] = {}
    for claim in claims:
        hit = next((row for row in remaining if str(row.get("claim") or "") == claim), None)
        if hit is None:
            wanted = _normalise_claim(claim)
            hit = next((row for row in remaining if _normalise_claim(str(row.get("claim") or "")) == wanted), None)
        if hit is None:
            best, best_ratio = None, 0.0
            for row in remaining:
                ratio = SequenceMatcher(None, _normalise_claim(claim), _normalise_claim(str(row.get("claim") or ""))).ratio()
                if ratio > best_ratio:
                    best, best_ratio = row, ratio
            hit = best if best_ratio >= 0.85 else None
        matched[claim] = hit
        if hit is not None:
            remaining.remove(hit)
    return matched

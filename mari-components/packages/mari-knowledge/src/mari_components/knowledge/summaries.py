"""Digest summarization and evidence-linked impact assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from mari_components.errors import MalformedModelOutput
from mari_components.types import Evidence, KnowledgeDocument
from .facts import _evidence
from .prompting import JsonGenerator, bounded_documents, documents_json, require_object


DIGEST_VERSION = "digest-summary-v1"
IMPACT_VERSION = "impact-assessment-v2"

#: How hard a changed claim hits one document, strongest first. The console
#: buckets an analysis by exactly these three and colours the graph by them.
IMPACT_SEVERITIES = ("update-required", "review", "minor")

#: Words a smaller model reaches for instead of the three above. Keys are
#: casefolded and space-separated; anything not here falls back to "minor",
#: which is the impact equivalent of check_claims' "uncertain": a row we keep
#: in the report rather than a verdict we invent.
_SEVERITY_WORDS = {
    "contradicted": "update-required",
    "contradicts": "update-required",
    "contradiction": "update-required",
    "critical": "update-required",
    "high": "update-required",
    "breaking": "update-required",
    "stale": "update-required",
    "needs update": "review",
    "medium": "review",
    "moderate": "review",
    "depends": "review",
    "dependent": "review",
    "related": "review",
    "affected": "review",
}


@dataclass(frozen=True, slots=True)
class DigestTopic:
    title: str
    summary: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class DigestSummary:
    summary: str
    topics: tuple[DigestTopic, ...]
    evidence: tuple[Evidence, ...]
    prompt_version: str = DIGEST_VERSION


@dataclass(frozen=True, slots=True)
class ImpactedDocument:
    """One supplied document and how the changed claim lands on it."""

    document_id: str
    #: One of IMPACT_SEVERITIES.
    severity: str
    #: Why, in the model's own words, for the reader who has to act on it.
    reason: str


@dataclass(frozen=True, slots=True)
class ImpactAssessment:
    summary: str
    #: The ids in `documents`, strongest severity first. Kept as its own field
    #: because callers that only need "what does this touch" predate the
    #: per-document verdicts and still read this.
    affected_document_ids: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    documents: tuple[ImpactedDocument, ...] = ()
    prompt_version: str = IMPACT_VERSION


def summarize_digest(documents: Iterable[KnowledgeDocument], *, generate_json: JsonGenerator, maximum_documents: int = 50, maximum_characters: int = 60_000) -> DigestSummary:
    bounded = bounded_documents(documents, maximum_documents=maximum_documents, maximum_characters=maximum_characters)
    allowed = {document.external_id: document for document in bounded}
    prompt = (
        "Summarize meaningful product-knowledge changes without inventing activity. Return concise topics and evidence. "
        'Return JSON {"summary":"...","topics":[{"title":"...","summary":"...",'
        '"evidence":[...]}],"evidence":[...]}.\nDocuments:\n' + documents_json(bounded)
    )
    value = require_object(generate_json(prompt, DIGEST_VERSION), recipe=DIGEST_VERSION)
    topics = value.get("topics")
    if not str(value.get("summary") or "").strip() or not isinstance(topics, list):
        raise MalformedModelOutput("digest summary and topics are required")
    parsed: list[DigestTopic] = []
    for topic in topics:
        if not isinstance(topic, dict):
            raise MalformedModelOutput("each digest topic must be an object")
        title = str(topic.get("title") or "").strip()
        summary = str(topic.get("summary") or "").strip()
        if not title or not summary:
            raise MalformedModelOutput("digest topic title and summary are required")
        parsed.append(DigestTopic(title, summary, _evidence(topic.get("evidence"), allowed, recipe=DIGEST_VERSION)))
    if not parsed:
        raise MalformedModelOutput("at least one digest topic is required")
    return DigestSummary(str(value["summary"]).strip(), tuple(parsed), _evidence(value.get("evidence"), allowed, recipe=DIGEST_VERSION))


def _severity(value: object) -> str:
    """One of IMPACT_SEVERITIES, from whatever word the model used."""
    text = " ".join(str(value or "").casefold().replace("-", " ").replace("_", " ").split())
    if not text:
        return "minor"
    hyphenated = text.replace(" ", "-")
    if hyphenated in IMPACT_SEVERITIES:
        return hyphenated
    return _SEVERITY_WORDS.get(text, "minor")


def _unique_titles(allowed: dict[str, KnowledgeDocument]) -> dict[str, str]:
    """Title → document id, for titles only one document carries.

    Smaller models answer with the title they were shown instead of the id
    beside it. That is recognisable when exactly one document holds the title;
    two documents called "Pricing" make it a guess, so it is not made.
    """
    counts: dict[str, int] = {}
    for document in allowed.values():
        counts[document.title.strip().casefold()] = counts.get(document.title.strip().casefold(), 0) + 1
    return {
        document.title.strip().casefold(): document_id
        for document_id, document in allowed.items()
        if counts[document.title.strip().casefold()] == 1 and document.title.strip()
    }


def assess_impact(changed_claim: str, documents: Iterable[KnowledgeDocument], *, generate_json: JsonGenerator, maximum_documents: int = 50, maximum_characters: int = 60_000) -> ImpactAssessment:
    """Which supplied documents a changed claim hits, and how hard.

    Tolerant in the same places check_claims is: a row naming a document that
    was never supplied is dropped, a severity word outside the scale falls back
    to "minor", and evidence that cannot be traced to a document is left off
    rather than taking the whole analysis down with it. A summary and a list
    are still required, and a list in which no row names a supplied document is
    still malformed: that is a model answering about a different corpus.
    """
    bounded = bounded_documents(documents, maximum_documents=maximum_documents, maximum_characters=maximum_characters)
    allowed = {document.external_id: document for document in bounded}
    titles = _unique_titles(allowed)
    prompt = (
        "Assess how the changed claim lands on each supplied document. Do not name documents outside the input, "
        "and return a row only for documents the claim actually touches. "
        'Severity is "update-required" when the document states the old value the claim changes, '
        '"review" when the document depends on the claim without stating it, and "minor" otherwise. '
        # A summary is a sentence over a list, and a reason is a line in a
        # drawer. Left unbounded, a model spends its whole generation budget on
        # prose and the JSON is cut off mid-row, which loses every verdict in
        # the run rather than making one of them long.
        "Keep the summary to two sentences at most. "
        "Give each row a reason a reader can act on, at most fifteen words. "
        'Return JSON {"summary":"...","documents":[{"document_id":"...",'
        '"severity":"update-required|review|minor","reason":"..."}],"evidence":[...]}.\nChanged claim:\n'
        + changed_claim.strip() + "\nDocuments:\n" + documents_json(bounded)
    )
    value = require_object(generate_json(prompt, IMPACT_VERSION), recipe=IMPACT_VERSION)
    # "affected_document_ids" is the v1 shape: a bare id list, no verdicts. It
    # is still read so a model pinned to the old prompt keeps working.
    rows = value.get("documents")
    if rows is None:
        rows = value.get("affected_document_ids")
    summary = str(value.get("summary") or "").strip()
    if not summary or not isinstance(rows, list):
        raise MalformedModelOutput("impact summary and affected documents are required")

    parsed: dict[str, ImpactedDocument] = {}
    for row in rows:
        if isinstance(row, str):
            row = {"document_id": row}
        if not isinstance(row, dict):
            continue
        key = str(row.get("document_id") or row.get("id") or "").strip()
        document_id = key if key in allowed else titles.get(
            (key or str(row.get("title") or "")).strip().casefold())
        if document_id is None:
            continue
        severity = _severity(row.get("severity"))
        reason = str(row.get("reason") or "").strip() or "The model did not say why."
        seen = parsed.get(document_id)
        # A document named twice keeps the harder verdict: the reader has to
        # act on the worst thing the analysis found in it, not the last.
        if seen and IMPACT_SEVERITIES.index(seen.severity) <= IMPACT_SEVERITIES.index(severity):
            continue
        parsed[document_id] = ImpactedDocument(document_id, severity, reason)
    if rows and not parsed:
        raise MalformedModelOutput("impact assessment named no supplied document")

    ordered = tuple(sorted(parsed.values(), key=lambda row: IMPACT_SEVERITIES.index(row.severity)))
    evidence: tuple[Evidence, ...] = ()
    if value.get("evidence"):
        try:
            evidence = _evidence(value.get("evidence"), allowed, recipe=IMPACT_VERSION)
        except MalformedModelOutput:
            # Same call as check_claims makes: unverifiable citations are not
            # findings, and they are not a reason to lose the verdicts either.
            evidence = ()
    return ImpactAssessment(summary, tuple(row.document_id for row in ordered), evidence, ordered)

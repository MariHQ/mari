"""Grounded answer generation and reusable FAQ candidate mining."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from mari_components.errors import MalformedModelOutput
from mari_components.types import AnswerCandidate, Evidence, KnowledgeDocument
from .facts import _evidence
from .prompting import JsonGenerator, bounded_documents, documents_json, require_list, require_object
from .scoring import evidence_confidence


ANSWER_VERSION = "grounded-answer-v2"
FAQ_VERSION = "faq-mine-v2"


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    answer: str
    evidence: tuple[Evidence, ...]
    confidence: float
    prompt_version: str = ANSWER_VERSION


def answer_question(question: str, documents: Iterable[KnowledgeDocument], *, generate_json: JsonGenerator, maximum_documents: int = 20, maximum_characters: int = 40_000) -> GroundedAnswer:
    question = question.strip()
    if not question:
        raise ValueError("question is required")
    bounded = bounded_documents(documents, maximum_documents=maximum_documents, maximum_characters=maximum_characters)
    allowed = {document.external_id: document for document in bounded}
    prompt = (
        "Answer only from the supplied product knowledge. Say that the evidence is insufficient when necessary. "
        "Cite exact document ids and quotes. "
        'Return JSON {"answer":"...","evidence":[...]}.\nQuestion:\n'
        + question + "\nDocuments:\n" + documents_json(bounded)
    )
    value = require_object(generate_json(prompt, ANSWER_VERSION), recipe=ANSWER_VERSION)
    answer = str(value.get("answer") or "").strip()
    if not answer:
        raise MalformedModelOutput("grounded answer text is required")
    evidence = () if not value.get("evidence") else _evidence(value.get("evidence"), allowed, recipe=ANSWER_VERSION)
    return GroundedAnswer(answer, evidence, evidence_confidence(answer, evidence))


def mine_answers(documents: Iterable[KnowledgeDocument], *, generate_json: JsonGenerator, maximum_documents: int = 50, maximum_characters: int = 60_000, maximum_answers: int = 8) -> tuple[AnswerCandidate, ...]:
    bounded = bounded_documents(documents, maximum_documents=maximum_documents, maximum_characters=maximum_characters)
    allowed = {document.external_id: document for document in bounded}
    # The bound lives in the prompt, not only in a slice afterwards: an
    # unbounded request lets the model write for as long as it likes, and on a
    # local model that ran past any honest timeout before the JSON closed.
    prompt = (
        "Mine recurring product questions that the documents answer directly. Each answer must be independently useful and evidenced. "
        f"Return AT MOST {max(1, maximum_answers)} answers, best first, each with one short evidence quote. "
        'Return JSON {"answers":[{"question":"...","answer":"...","evidence":[...]}]}.\nDocuments:\n'
        + documents_json(bounded)
    )
    rows = require_list(generate_json(prompt, FAQ_VERSION), "answers", recipe=FAQ_VERSION)
    output: list[AnswerCandidate] = []
    for row in rows[: max(1, maximum_answers)]:
        # Mining proposes candidates for a human review step; one row the
        # model mangled (a missing field, a citation of a document it was
        # never shown) drops that row, not the whole batch. A grounded
        # answer stays strict — this leniency is mining-only.
        question, answer = str(row.get("question") or "").strip(), str(row.get("answer") or "").strip()
        if not question or not answer:
            continue
        try:
            evidence = _evidence(row.get("evidence"), allowed, recipe=FAQ_VERSION)
        except MalformedModelOutput:
            continue
        output.append(AnswerCandidate(
            question, answer, evidence, evidence_confidence(answer, evidence),
        ))
    if rows and not output:
        raise MalformedModelOutput("no usable FAQ rows survived validation")
    return tuple(output)

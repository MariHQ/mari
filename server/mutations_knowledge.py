"""Mari — knowledge mutations: docs, facts, decisions, answers, glossary,
tags, tasks, lineage, digest, insights, watches."""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import re
import time
import typing as t

import strawberry

import flowengine
import llm
from db import actor_name, audit, exec_, jload, q, q1
from gqltypes import AnswerCandidate, ImpactDoc, ImpactResult
from queries import hybrid_search


# A document's metadata line is not a claim. The scanner is fed
# "[source] title: body", and for a GitHub PR the body IS the header line, so
# the model happily returned "PR #340 · petk · closed · updated
# 2016-01-17T01:57:54Z" as a fact — a row nobody can verify, and one the
# contradiction detector then compares digit by digit against its neighbour.
_META_CLAIM = re.compile(
    r"""(^\s*(PR|MR|Issue|Commit)\s*\#?\d)   # starts as a PR/issue/commit header
      | (\d{4}-\d{2}-\d{2}T\d{2}:\d{2})      # carries a raw ISO timestamp
      | (^[^.!?]*·[^.!?]*·)                  # a "a · b · c" pill line, not a sentence
    """,
    re.IGNORECASE | re.VERBOSE)


def is_claim(text: str) -> bool:
    """Is this a sentence someone could agree or disagree with? Guards the
    ledger against metadata the extractor echoed back at us."""
    claim = (text or "").strip()
    return bool(claim) and len(claim.split()) >= 4 and not _META_CLAIM.search(claim)

def _iso_date(value: str | None) -> str | None:
    """Accept an ISO date (or an ISO timestamp) and return YYYY-MM-DD; None for
    empty. Anything else is rejected loudly rather than stored as a string the
    console would later fail to sort."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise ValueError(f"Due date must be an ISO date (YYYY-MM-DD), got {text!r}") from None


# Vocabularies the editorial surfaces are allowed to store. Each mirrors a
# union the component library renders: a value outside one draws an unlabelled
# chip or a missing glyph, so it is rejected at write time rather than filtered
# out at read time.
_TONES = {"ink", "ok", "attention", "blocked", "info"}
_SEVERITIES = {"error", "warn", "advisory"}
_TEMPLATE_ICONS = {"clipboard", "git-fork", "shield-check", "file-text",
                   "sprout", "book-open", "megaphone"}


def _slug(value: str) -> str:
    """A stable url-safe key. Keys are addressed by mutations and stored on
    settings rows, so they must not carry whitespace or punctuation."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:64]


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


def _scan_batch(column: str, doc_ids: list[int] | None, limit: int) -> list[dict]:
    """The documents this scan should read.

    With `doc_ids` (a flow step's fetch_docs output) those documents, in the
    order given. Without, the least-recently-scanned documents first, newest
    breaking the tie — so every document is reached in turn instead of the two
    newest being re-read on every scan forever."""
    if doc_ids:
        rows = q(f"""SELECT id, title, snippet, body, source, updated_src FROM documents
                     WHERE id = ANY(%s) ORDER BY {column} NULLS FIRST,
                                                updated_src DESC NULLS LAST, id""",
                 (list(doc_ids),))
        return rows[:limit] if limit else rows
    return q(f"""SELECT id, title, snippet, body, source, updated_src FROM documents
                 ORDER BY {column} NULLS FIRST, updated_src DESC NULLS LAST, id
                 LIMIT %s""", (limit,))


def _mark_scanned(column: str, doc_ids: list[int]) -> None:
    """Record that the scanner read these documents. This is what makes the
    next scan pick different ones; without it the rotation above is a no-op."""
    if doc_ids:
        exec_(f"UPDATE documents SET {column} = now() WHERE id = ANY(%s)", (list(doc_ids),))


def _scan_concurrently(docs: list[dict], build_prompt, system: str) -> tuple[list[tuple[dict, t.Any]], int]:
    """Run one model call per document, at most SCAN_WORKERS at a time, and
    stop accepting new work once SCAN_DEADLINE has passed.

    Returns (results, unread) — `unread` is how many documents the deadline cut
    off. The caller reports it. A scan that ran out of time and said so is a
    scan someone can re-run; a scan that ran out of time and returned a smaller
    number is indistinguishable from a corpus with less in it."""
    if not docs:
        return [], 0
    results: list[tuple[dict, t.Any]] = []
    deadline = time.monotonic() + SCAN_DEADLINE
    with cf.ThreadPoolExecutor(max_workers=min(SCAN_WORKERS, len(docs)),
                               thread_name_prefix="mari-scan") as pool:
        futures = {pool.submit(llm.generate_json, build_prompt(d), system,
                               SCAN_CALL_TIMEOUT): d for d in docs}
        for future in cf.as_completed(futures):
            doc = futures[future]
            try:
                results.append((doc, future.result()))
            except Exception:  # noqa: BLE001 — one bad document must not end the scan
                results.append((doc, None))
            if time.monotonic() >= deadline:
                # Stop waiting. Calls already in flight finish into a result
                # nobody reads, which costs the model's time but not the
                # caller's; the count below is what the run reports.
                for pending in futures:
                    if not pending.done():
                        pending.cancel()
                break
    return results, len(docs) - len(results)


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

FALLBACK_CHANGES = {
    "tighten": [("in order to enable rotation", "to enable rotation", "Tighten")],
    "plain": [("utilize the new endpoint", "use the new endpoint", "Plain language")],
}


def llm_refine(doc: dict, skill: str) -> list[tuple[str, str, str]]:
    prompt = (
        f"{SKILL_PROMPTS.get(skill, SKILL_PROMPTS['tighten'])}\n\n"
        f"Document:\n{doc['title']}\n{doc['body'] or doc['snippet']}\n\n"
        'Return a JSON array of edits: [{"original": "...", "replacement": "...", "reason": "..."}]. '
        "original must be an exact substring of the document. At most 4 edits."
    )
    out = llm.generate_json(prompt, system="You are Mari, a precise technical editor.")
    edits = []
    if isinstance(out, list):
        for e in out:
            if isinstance(e, dict) and e.get("original") and e.get("replacement"):
                edits.append((str(e["original"])[:300], str(e["replacement"])[:300], str(e.get("reason", skill))[:120]))
    return edits or FALLBACK_CHANGES.get(skill, FALLBACK_CHANGES["tighten"])


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
    docs = _scan_batch("decisions_scanned_at", doc_ids, limit)
    if not docs:
        return 0, 0, ""
    existing = {r["statement"].lower() for r in q("SELECT statement FROM decisions")}

    def prompt_for(d: dict) -> str:
        text = (d["body"] or d["snippet"] or "").strip()
        return (
            f"Team document [{d['source']} · {d['updated_src']}] {d['title']}:\n{text[:1500]}\n\n"
            "Extract DECISIONS this document records the team making — things chosen, "
            "changed, or committed to. "
            'JSON: [{"statement": "declarative one-liner", "context": "1 sentence of why"}]. '
            f"At most {CLAIMS_PER_DOC}; skip anything that is already obvious policy. "
            "An empty list is the right answer for a document that records none."
        )

    results, unread = _scan_concurrently(
        docs, prompt_for, "You mine team knowledge for decisions worth ratifying.")

    added = 0
    for doc, out in results:
        if not isinstance(out, list):
            continue
        for item in out[:CLAIMS_PER_DOC]:
            # FACT-3: a model that answers with a list of strings is not a
            # crash, it is a model answering in a shape we did not ask for.
            if not isinstance(item, dict):
                continue
            stmt = str(item.get("statement", "")).strip()[:200]
            if not stmt or stmt.lower() in existing:
                continue
            exec_("""INSERT INTO decisions (statement, context, status, source_label, owners)
                     VALUES (%s, %s, 'proposed', %s, %s) ON CONFLICT (statement) DO NOTHING""",
                  (stmt, str(item.get("context", ""))[:400],
                   ("Mari scan · " + doc["title"])[:120], [actor_name()]))
            existing.add(stmt.lower())
            added += 1

    _mark_scanned("decisions_scanned_at", [doc["id"] for doc, _ in results])
    note = (f"{unread} document{'' if unread == 1 else 's'} not read — the scan hit its "
            f"{int(SCAN_DEADLINE)}s budget; run it again to continue") if unread else ""
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
    docs = [d for d in _scan_batch("facts_scanned_at", doc_ids, limit)
            if (d["body"] or d["snippet"] or "").strip()]
    if not docs:
        return 0, 0, ""
    existing = {r["claim"].lower() for r in q("SELECT claim FROM facts")}

    def prompt_for(d: dict) -> str:
        text = (d["body"] or d["snippet"] or "").strip()
        return (
            f"Team document [{d['source']}] {d['title']}:\n{text[:1500]}\n\n"
            "Extract atomic, checkable FACTS (numbers, limits, defaults, policies) "
            "stated by THIS document. "
            "Write each claim as a plain English sentence a person could agree or disagree with; "
            "never copy a document's metadata line (PR/issue headers, authors, timestamps, status). "
            f'JSON: [{{"claim": "one factual sentence"}}]. At most {CLAIMS_PER_DOC}; no opinions. '
            "An empty list is the right answer for a document that states none."
        )

    results, unread = _scan_concurrently(
        docs, prompt_for, "You extract verifiable facts from documentation.")

    # The ceiling is per document (FACT-2). A shared budget meant the first
    # document's claims displaced the fifth document's, and the fifth
    # document was read for nothing — silently, since the count it returned
    # looked exactly like a document that had no claims in it.
    added = 0
    for doc, out in results:
        if not isinstance(out, list):
            continue
        for item in out[:CLAIMS_PER_DOC]:
            if not isinstance(item, dict):  # FACT-3
                continue
            claim = str(item.get("claim", "")).strip()[:200]
            if not is_claim(claim) or claim.lower() in existing:
                continue
            # `source` stays the human label it always was; `document_id`
            # is the key, and it is the document this call actually read.
            exec_("""INSERT INTO facts (claim, source, owner_name, owner_tint, status, verified, document_id)
                     VALUES (%s, %s, %s, 1, 'Needs review', '—', %s) ON CONFLICT (claim) DO NOTHING""",
                  (claim, ("Mari scan · " + doc["title"])[:80], actor_name(), doc["id"]))
            existing.add(claim.lower())
            added += 1

    _mark_scanned("facts_scanned_at", [doc["id"] for doc, _ in results])
    note = (f"{unread} document{'' if unread == 1 else 's'} not read — the scan hit its "
            f"{int(SCAN_DEADLINE)}s budget; run it again to continue") if unread else ""
    audit("scanned for facts", f"{added} candidates from {len(results)} documents"
                               + (f" ({note})" if note else ""))
    return added, len(results), note


@strawberry.type
class MutKnowledge:
    @strawberry.mutation
    def set_task_done(self, id: int, done: bool) -> bool:
        exec_("UPDATE tasks SET done = %s WHERE id = %s", (done, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s, title FROM tasks WHERE id = %s",
              (actor_name(), "completed" if done else "reopened", id))
        return True

    @strawberry.mutation
    def clear_done_tasks(self) -> int:
        n = q1("SELECT count(*) AS n FROM tasks WHERE done")["n"]
        exec_("DELETE FROM tasks WHERE done")
        if n:
            audit("cleared done tasks", f"{n} tasks")
        return n

    @strawberry.mutation
    def verify_fact(self, id: int) -> bool:
        # verified_at is a DATE; the legacy `verified` text column held a
        # display string the console could neither re-format nor sort on.
        exec_("UPDATE facts SET status = 'Verified', verified_at = current_date WHERE id = %s", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'verified fact', claim FROM facts WHERE id = %s", (actor_name(), id))
        return True

    @strawberry.mutation
    def create_task(self, title: str, kind: str = "factcheck", kind_label: str = "Fact check",
                    assignee: str = "", due: str | None = None) -> bool:
        """`due` is an ISO date (YYYY-MM-DD) or null. Null is the default:
        nothing in the product assigns a deadline on its own, so a task only
        has one when whoever created it said so.

        `assignee` defaults to unassigned for the same reason — the product
        does not know who should do this, and naming a person nobody chose put
        one developer's name on tasks in every workspace that installed it."""
        assignee = assignee.strip()
        initials = "".join(w[0].upper() for w in assignee.split()[:2])
        exec_("""INSERT INTO tasks (title, assignee, assignee_initials, assignee_tint, kind, kind_label, due_date)
                 VALUES (%s, %s, %s, 1, %s, %s, %s) ON CONFLICT (title) DO NOTHING""",
              (title, assignee, initials, kind, kind_label, _iso_date(due)))
        audit("created task", title, detail=[("Assignee", assignee or "(unassigned)"), ("Kind", kind_label),
                                             ("Due", _iso_date(due) or "(no deadline)")])
        return True

    @strawberry.mutation
    def set_task_due(self, id: int, due: str | None = None) -> bool:
        """Set or (with null/empty) clear a task's deadline. ISO date in, ISO
        date out — the console formats and sorts on the raw value."""
        value = _iso_date(due)
        before = q1("SELECT title, due_date FROM tasks WHERE id = %s", (id,))
        if not before:
            return False
        exec_("UPDATE tasks SET due_date = %s WHERE id = %s", (value, id))
        audit("set task due date" if value else "cleared task due date", before["title"],
              detail=[("Previous due", before["due_date"].isoformat() if before["due_date"] else "(none)"),
                      ("New due", value or "(none)")])
        return True

    @strawberry.mutation
    def add_fact(self, claim: str, source: str, owner: str = "",
                 document_id: int | None = None) -> bool:
        """`owner` is who stands behind the claim. It defaults to whoever added
        it — a true statement about this row — rather than to a name hardcoded
        at build time that no workspace ever chose.

        `document_id` is the document this claim was read out of — pass it
        only when there is one. A claim typed in by hand cites no document and
        keeps NULL, which is the truth about it: Doc Review lists a document's
        claims by this key, so a guessed id would put someone else's claim
        under this title."""
        doc_id = document_id or None
        if doc_id and not q1("SELECT 1 FROM documents WHERE id = %s", (doc_id,)):
            raise ValueError(f"No document {doc_id} to attribute this claim to")
        owner = owner.strip() or actor_name()
        exec_("""INSERT INTO facts (claim, source, owner_name, owner_tint, status, verified, document_id)
                 VALUES (%s, %s, %s, 1, 'Needs review', '—', %s) ON CONFLICT (claim) DO NOTHING""",
              (claim, source, owner, doc_id))
        audit("added fact", claim, detail=[("Owner", owner), ("Source", source)])
        return True

    # ——— glossary CRUD ———
    @strawberry.mutation
    def upsert_glossary(self, term: str, definition: str, id: int | None = None,
                        evidence: str = "", evidence_doc_id: int | None = None) -> bool:
        """`evidence` is the document a term was mined from — pass it only when
        there is one. A term typed in by hand keeps '' and cites nothing, which
        is the truth about it; an existing citation is never overwritten with
        a blank."""
        doc_id = evidence_doc_id or None
        if doc_id and not q1("SELECT 1 FROM documents WHERE id = %s", (doc_id,)):
            raise ValueError(f"No document {doc_id} to cite as evidence")
        if id:
            exec_("""UPDATE glossary SET term = %s, definition = %s, updated = now(),
                     evidence = CASE WHEN %s <> '' THEN %s ELSE evidence END,
                     evidence_doc_id = coalesce(%s, evidence_doc_id) WHERE id = %s""",
                  (term, definition, evidence, evidence, doc_id, id))
            audit("updated glossary term", term)
        else:
            exec_("""INSERT INTO glossary (term, definition, owner_name, updated, evidence, evidence_doc_id)
                     VALUES (%s, %s, %s, now(), %s, %s)
                     ON CONFLICT (term) DO UPDATE SET definition = EXCLUDED.definition, updated = now(),
                       evidence = CASE WHEN EXCLUDED.evidence <> '' THEN EXCLUDED.evidence ELSE glossary.evidence END,
                       evidence_doc_id = coalesce(EXCLUDED.evidence_doc_id, glossary.evidence_doc_id)""",
                  (term, definition, actor_name(), evidence, doc_id))
            audit("added glossary term", term, detail=[("Evidence", evidence or "(none)")])
        return True

    @strawberry.mutation
    def delete_glossary(self, id: int) -> bool:
        exec_("DELETE FROM glossary WHERE id = %s", (id,))
        audit("deleted glossary term", f"#{id}")
        return True

    # ——— style guides, rule registry, voice layer ———
    @strawberry.mutation
    def upsert_style_guide(self, key: str, name: str, description: str = "",
                           tone: str = "ink") -> bool:
        """Create or edit a style pack. Packs written here are `builtin = false`
        — the guides tab distinguishes what the workspace wrote from what the
        product ships, and an edit to a shipped pack does not launder it."""
        slug = _slug(key)
        if not slug or not name.strip():
            raise ValueError("A style guide needs a key and a name")
        if tone not in _TONES:
            raise ValueError(f"tone must be one of {', '.join(sorted(_TONES))}")
        exec_("""INSERT INTO style_guides (key, name, description, tone, builtin, sort)
                 VALUES (%s, %s, %s, %s, false, 200)
                 ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name,
                   description = EXCLUDED.description, tone = EXCLUDED.tone""",
              (slug, name.strip(), description.strip(), tone))
        audit("saved style guide", name.strip(), detail=[("Key", slug), ("Tone", tone)])
        return True

    @strawberry.mutation
    def delete_style_guide(self, key: str) -> bool:
        """Delete a pack and its rules. Clears the workspace default if it was
        the one adopted, so nothing points at a pack that no longer exists."""
        guide = q1("SELECT name FROM style_guides WHERE key = %s", (key,))
        if not guide:
            return False
        n = int((q1("SELECT count(*) AS n FROM style_rules WHERE guide_key = %s", (key,)) or {"n": 0})["n"])
        exec_("DELETE FROM style_guides WHERE key = %s", (key,))  # rules cascade
        exec_("""UPDATE settings SET value = value || '{"default_pack":""}'
                 WHERE key = 'style_guide' AND value->>'default_pack' = %s""", (key,))
        audit("deleted style guide", guide["name"], detail=[("Key", key), ("Rules removed", n)])
        return True

    @strawberry.mutation
    def upsert_style_rule(self, id: str, guide_key: str, description: str,
                          family: str = "", severity: str = "advisory",
                          pack: str = "", suggestion: str = "") -> bool:
        """Add or edit one rule in the registry. The rule must belong to a pack
        that exists — the count on the Library's tab strip is this table's row
        count, and an orphan rule would inflate it."""
        if not q1("SELECT 1 FROM style_guides WHERE key = %s", (guide_key,)):
            raise ValueError(f"No style guide '{guide_key}' to add this rule to")
        if severity not in _SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(sorted(_SEVERITIES))}")
        if not id.strip() or not description.strip():
            raise ValueError("A rule needs an id and a description")
        exec_("""INSERT INTO style_rules (id, guide_key, family, severity, description, pack, suggestion, sort)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, 200)
                 ON CONFLICT (id) DO UPDATE SET guide_key = EXCLUDED.guide_key,
                   family = EXCLUDED.family, severity = EXCLUDED.severity,
                   description = EXCLUDED.description, pack = EXCLUDED.pack,
                   suggestion = EXCLUDED.suggestion""",
              (id.strip(), guide_key, family.strip(), severity, description.strip(),
               pack.strip(), suggestion.strip()))
        audit("saved style rule", id.strip(), detail=[("Guide", guide_key), ("Severity", severity)])
        return True

    @strawberry.mutation
    def delete_style_rule(self, id: str) -> bool:
        if not q1("SELECT 1 FROM style_rules WHERE id = %s", (id,)):
            return False
        exec_("DELETE FROM style_rules WHERE id = %s", (id,))
        audit("deleted style rule", id)
        return True

    @strawberry.mutation
    def set_default_style_pack(self, key: str) -> bool:
        """Adopt a pack as the project default, or clear the choice with ''.
        A key with no pack behind it is rejected rather than stored."""
        pack = key.strip()
        if pack and not q1("SELECT 1 FROM style_guides WHERE key = %s", (pack,)):
            raise ValueError(f"No style guide '{pack}' to adopt")
        before = q1("SELECT value->>'default_pack' AS pack FROM settings WHERE key = 'style_guide'")
        exec_("""INSERT INTO settings (key, value) VALUES ('style_guide', %s)
                 ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value""",
              (json.dumps({"default_pack": pack}),))
        audit("adopted style pack" if pack else "cleared style pack", pack or "(none)",
              detail=[("Previous", (before or {}).get("pack") or "(none)"), ("New", pack or "(none)")])
        return True

    @strawberry.mutation
    def set_voice_layer(self, voice: str = "", terms: str = "", banned: str = "",
                        inclusive: bool = False, jargon: bool = False,
                        sentence_case: bool = False) -> bool:
        """Write the workspace's voice layer. The whole layer is replaced, not
        merged: the panel posts every field it renders, and merging would leave
        a cleared field looking like it was never edited."""
        layer = {"voice": voice.strip(), "terms": terms.strip(), "banned": banned.strip(),
                 "inclusive": bool(inclusive), "jargon": bool(jargon),
                 "sentence_case": bool(sentence_case)}
        exec_("""INSERT INTO settings (key, value) VALUES ('voice', %s)
                 ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps(layer),))
        audit("saved voice layer", "Style guides",
              detail=[("Enforcement", ", ".join(k for k in ("inclusive", "jargon", "sentence_case")
                                                if layer[k]) or "(none)")])
        return True

    # ——— document templates ———
    @strawberry.mutation
    def upsert_document_template(self, key: str, name: str, category: str = "",
                                 description: str = "", sections: list[str] | None = None,
                                 icon: str = "file-text") -> bool:
        """Create or edit a document scaffold. Templates saved here are
        `standard = false`; the shipped set stays labelled as shipped."""
        slug = _slug(key)
        if not slug or not name.strip():
            raise ValueError("A template needs a key and a name")
        if icon not in _TEMPLATE_ICONS:
            raise ValueError(f"icon must be one of {', '.join(sorted(_TEMPLATE_ICONS))}")
        rows = [s.strip() for s in (sections or []) if s.strip()]
        exec_("""INSERT INTO document_templates (key, name, category, description, sections, icon, standard, sort)
                 VALUES (%s, %s, %s, %s, %s, %s, false, 200)
                 ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, category = EXCLUDED.category,
                   description = EXCLUDED.description, sections = EXCLUDED.sections, icon = EXCLUDED.icon""",
              (slug, name.strip(), category.strip(), description.strip(), json.dumps(rows), icon))
        audit("saved document template", name.strip(),
              detail=[("Key", slug), ("Category", category.strip() or "(none)"), ("Sections", len(rows))])
        return True

    @strawberry.mutation
    def delete_document_template(self, key: str) -> bool:
        row = q1("SELECT name, standard FROM document_templates WHERE key = %s", (key,))
        if not row:
            return False
        exec_("DELETE FROM document_templates WHERE key = %s", (key,))
        audit("deleted document template", row["name"],
              detail=[("Key", key), ("Shipped with the product", "yes" if row["standard"] else "no")])
        return True

    # ——— tags ———
    @strawberry.mutation
    def set_tag_weight(self, tag: str, weight: float) -> bool:
        exec_("UPDATE tag_definitions SET search_weight = %s WHERE tag = %s", (weight, tag))
        audit("set tag weight", f"{tag} → {weight}")
        return True

    @strawberry.mutation
    def upsert_tag_def(self, tag: str, label: str, kind: str = "neutral",
                       search_weight: float = 1.0, behaviors: str = "") -> bool:
        exec_("""INSERT INTO tag_definitions (tag, label, kind, search_weight, is_default, behaviors)
                 VALUES (%s, %s, %s, %s, false, %s)
                 ON CONFLICT (tag) DO UPDATE SET label = EXCLUDED.label, kind = EXCLUDED.kind,
                   search_weight = EXCLUDED.search_weight, behaviors = EXCLUDED.behaviors""",
              (tag, label, kind, search_weight, behaviors))
        audit("saved tag definition", tag)
        return True

    @strawberry.mutation
    def delete_tag_def(self, tag: str) -> bool:
        exec_("DELETE FROM tag_definitions WHERE tag = %s AND NOT is_default", (tag,))
        audit("deleted tag definition", tag)
        return True

    @strawberry.mutation
    def tag_document(self, document_id: int, tag: str) -> list[str]:
        """Assign a tag to a document — e.g. 'customer-facing', which is what
        the Publish build selects by. Before this mutation existed, the only
        way to set it was the agent's chat tool loop or a workflow's tag step;
        the Knowledge inspector's tag picker had nothing to call, so it only
        ever changed its own local state. Returns the document's tags after
        the change, so the caller doesn't need a second round-trip."""
        clean = re.sub(r"[^a-z0-9\-]", "", tag.lower().strip())
        if not clean:
            raise ValueError("Not a valid tag.")
        exec_("INSERT INTO tags (document_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING",
              (document_id, clean))
        row = q1("SELECT title FROM documents WHERE id = %s", (document_id,))
        audit(f"tagged {clean}", row["title"] if row else f"document {document_id}")
        return [r["tag"] for r in q("SELECT tag FROM tags WHERE document_id = %s ORDER BY tag", (document_id,))]

    @strawberry.mutation
    def untag_document(self, document_id: int, tag: str) -> list[str]:
        exec_("DELETE FROM tags WHERE document_id = %s AND tag = %s", (document_id, tag.lower().strip()))
        row = q1("SELECT title FROM documents WHERE id = %s", (document_id,))
        audit(f"untagged {tag}", row["title"] if row else f"document {document_id}")
        return [r["tag"] for r in q("SELECT tag FROM tags WHERE document_id = %s ORDER BY tag", (document_id,))]

    # ——— doc review ———
    @strawberry.mutation
    def update_document(self, id: int, body: str, title: str | None = None) -> bool:
        """Editor save: persist body (and title), re-embed, log the revision."""
        if title:
            exec_("UPDATE documents SET body = %s, title = %s, updated_src = now() WHERE id = %s", (body, title, id))
        else:
            exec_("UPDATE documents SET body = %s, updated_src = now() WHERE id = %s", (body, id))
        vec = llm.embed(body[:3000])
        if vec:
            exec_("UPDATE documents SET embedding = %s::vector WHERE id = %s", (str(vec), id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'edited', title FROM documents WHERE id = %s", (actor_name(), id))
        return True

    @strawberry.mutation
    def set_change_status(self, id: int, status: str) -> bool:
        exec_("UPDATE changes SET status = %s WHERE id = %s", (status, id))
        if status == "accepted":
            exec_("""UPDATE documents d SET body = replace(d.body, c.original, c.replacement)
                     FROM changes c WHERE c.id = %s AND d.id = c.document_id""", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s || ' change', original FROM changes WHERE id = %s",
              (actor_name(), status, id))
        return True

    @strawberry.mutation
    def accept_all_changes(self, document_id: int) -> bool:
        exec_("""UPDATE documents d SET body = (
                   SELECT reduce.body FROM (
                     SELECT %s AS did) x,
                   LATERAL (
                     SELECT coalesce(
                       (WITH RECURSIVE r AS (
                          SELECT 0 AS i, d.body AS body
                          UNION ALL
                          SELECT r.i + 1, replace(r.body, c.original, c.replacement)
                          FROM r JOIN (
                            SELECT row_number() OVER (ORDER BY id) - 1 AS rn, original, replacement
                            FROM changes WHERE document_id = %s AND status = 'pending') c ON c.rn = r.i)
                        SELECT body FROM r ORDER BY i DESC LIMIT 1), d.body) AS body) reduce)
                 WHERE d.id = %s""", (document_id, document_id, document_id))
        exec_("UPDATE changes SET status = 'accepted' WHERE document_id = %s AND status = 'pending'", (document_id,))
        audit("accepted all changes", f"document #{document_id}")
        return True

    @strawberry.mutation
    def run_refinement(self, document_id: int, skill: str = "tighten") -> int:
        doc = q1("SELECT * FROM documents WHERE id = %s", (document_id,))
        if not doc:
            return 0
        edits = llm_refine(doc, skill)
        for original, replacement, reason in edits:
            exec_("""INSERT INTO changes (document_id, original, replacement, reason)
                     VALUES (%s, %s, %s, %s) ON CONFLICT (document_id, original) DO NOTHING""",
                  (document_id, original, replacement, reason))
        audit(f"ran {skill} refinement", doc["title"])
        return len(edits)

    @strawberry.mutation
    def fact_check(self, document_id: int) -> int:
        doc = q1("SELECT * FROM documents WHERE id = %s", (document_id,))
        if not doc:
            return 0
        facts = q("SELECT claim FROM facts WHERE status = 'Verified'")
        prompt = (
            f"Document:\n{doc['title']}\n{doc['body'] or doc['snippet']}\n\n"
            "Verified facts:\n" + "\n".join(f"- {f['claim']}" for f in facts) + "\n\n"
            'List contradictions between the document and the facts as JSON: '
            '[{"text": "exact phrase from document", "note": "which fact it contradicts"}]. Empty array if none.'
        )
        out = llm.generate_json(prompt, system="You are Mari, a rigorous fact checker.")
        found = 0
        if isinstance(out, list):
            for f in out[:5]:
                if isinstance(f, dict) and f.get("text"):
                    exec_("""INSERT INTO findings (document_id, kind, severity, text, note)
                             VALUES (%s, 'fact', 'error', %s, %s) ON CONFLICT (document_id, text) DO NOTHING""",
                          (document_id, str(f["text"])[:200], str(f.get("note", ""))[:300]))
                    found += 1
        audit("ran fact check", doc["title"])
        return found

    # ——— lineage / semantic links ———
    @strawberry.mutation
    def derive_links(self) -> int:
        docs = q("SELECT external_id, title, snippet FROM documents ORDER BY id")
        existing = {(r["f"], r["t"]) for r in q(
            """SELECT f.external_id AS f, t.external_id AS t FROM edges e
               JOIN documents f ON f.id = e.from_doc JOIN documents t ON t.id = e.to_doc""")}
        listing = "\n".join(f"- {d['external_id']}: {d['title']} — {d['snippet'][:100]}" for d in docs)
        prompt = (
            f"Documents:\n{listing}\n\n"
            'Propose semantic links between related documents as JSON: '
            '[{"from": "external_id", "to": "external_id", "rel": "reference"}]. '
            "Only strong topical relationships. At most 3."
        )
        out = llm.generate_json(prompt, system="You derive knowledge-graph edges.")
        added = 0
        if isinstance(out, list):
            ids = {d["external_id"] for d in docs}
            for e in out:
                f_, t_ = e.get("from"), e.get("to")
                if f_ in ids and t_ in ids and f_ != t_ and (f_, t_) not in existing:
                    exec_("""INSERT INTO edges (from_doc, to_doc, rel, day, curve, meta, created_at)
                             SELECT f.id, t.id, 'reference', 14, 20, '{"derived":"llm","confidence":0.7}', CURRENT_DATE
                             FROM documents f, documents t WHERE f.external_id = %s AND t.external_id = %s""",
                          (f_, t_))
                    added += 1
        audit("derived semantic links", f"{added} new edges")
        return added

    @strawberry.mutation
    def pin_node(self, document_id: int, x: float, y: float) -> bool:
        exec_("UPDATE documents SET graph_x = %s, graph_y = %s WHERE id = %s", (x, y, document_id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'pinned graph node', title FROM documents WHERE id = %s",
              (actor_name(), document_id))
        return True

    @strawberry.mutation
    def unpin_node(self, document_id: int) -> bool:
        exec_("UPDATE documents SET graph_x = NULL, graph_y = NULL WHERE id = %s", (document_id,))
        return True

    @strawberry.mutation
    def save_graph_view(self, name: str, state: str) -> int:
        exec_("""INSERT INTO graph_views (name, state, created_by) VALUES (%s, %s::jsonb, %s)
                 ON CONFLICT (name) DO UPDATE SET state = EXCLUDED.state""", (name, state, actor_name()))
        audit("saved graph view", name)
        return (q1("SELECT id FROM graph_views WHERE name = %s", (name,)) or {"id": 0})["id"]

    @strawberry.mutation
    def delete_graph_view(self, id: int) -> bool:
        exec_("DELETE FROM graph_views WHERE id = %s", (id,))
        return True

    # ——— digest ———
    @strawberry.mutation
    def regenerate_digest(self) -> bool:
        docs = q("SELECT title, snippet, source FROM documents ORDER BY updated_src DESC LIMIT 8")
        facts = q("SELECT claim, status FROM facts")
        prompt = (
            "Recent documents:\n" + "\n".join(f"- [{d['source']}] {d['title']}: {d['snippet'][:80]}" for d in docs)
            + "\n\nFacts:\n" + "\n".join(f"- {f['claim']} ({f['status']})" for f in facts)
            + '\n\nWrite this week\'s digest as JSON: [{"title": "...", "summary": "2 sentences", '
            '"wheres": [{"source": "github|slack|docs|notion|granola|gdocs", "label": "..."}], '
            '"impact": [{"name": "service or doc", "tone": "#bf4f2e|#35549d|#5c7a4c|#c8973a"}]}]. Exactly 3 topics.'
        )
        out = llm.generate_json(prompt, system="You are Mari, summarizing the team's week.")
        if isinstance(out, list) and out:
            exec_("DELETE FROM digest_topics")
            for topic in out[:3]:
                exec_("INSERT INTO digest_topics (title, summary, wheres, impact) VALUES (%s, %s, %s, %s)",
                      (str(topic.get("title", "Untitled"))[:120], str(topic.get("summary", ""))[:500],
                       json.dumps(topic.get("wheres", [])), json.dumps(topic.get("impact", []))))
            audit("regenerated digest", f"{min(len(out),3)} topics")
            return True
        audit("digest regeneration failed (LLM unavailable)", "kept previous digest")
        return False

    # ——— impact analysis ———
    @strawberry.mutation
    def impact_analysis(self, claim: str) -> ImpactResult:
        rows = hybrid_search(claim, 6)
        listing = "\n".join(f"- [{r['source']}] {r['title']}: {r['snippet'][:90]}" for r in rows)
        prompt = (
            f'Asserted fact: "{claim}"\n\nDocuments:\n{listing}\n\n'
            'Which documents does this assertion impact? JSON: {"summary": "1 sentence", '
            '"docs": [{"title": "...", "source": "...", "severity": "update-required|review|minor", "reason": "..."}]}'
        )
        out = llm.generate_json(prompt, system="You analyze the blast radius of a changed fact.")
        audit("ran impact analysis", claim)
        if isinstance(out, dict) and out.get("docs"):
            return ImpactResult(
                claim=claim, summary=str(out.get("summary", "")),
                docs=[ImpactDoc(title=str(d.get("title", ""))[:120], source=str(d.get("source", "docs")),
                                severity=str(d.get("severity", "review")), reason=str(d.get("reason", ""))[:200])
                      for d in out["docs"][:8] if isinstance(d, dict)])
        return ImpactResult(claim=claim, summary=f"{len(rows)} related documents found by search.",
                            docs=[ImpactDoc(title=r["title"], source=r["source"], severity="review",
                                            reason="Matched by hybrid search") for r in rows])

    # ——— approved answers ———
    @strawberry.mutation
    def upsert_answer(self, question: str, answer: str, id: int | None = None) -> bool:
        if id:
            exec_("UPDATE approved_answers SET question = %s, answer = %s, updated = now() WHERE id = %s",
                  (question, answer, id))
        else:
            exec_("""INSERT INTO approved_answers (question, answer, status, owner_name, updated)
                     VALUES (%s, %s, 'draft', %s, now())
                     ON CONFLICT (question) DO UPDATE SET answer = EXCLUDED.answer, updated = now()""",
                  (question, answer, actor_name()))
        audit("drafted answer", question)
        return True

    @strawberry.mutation
    def set_answer_status(self, id: int, status: str) -> bool:
        exec_("UPDATE approved_answers SET status = %s, updated = now() WHERE id = %s", (status, id))
        if status == "approved":
            vec = None
            row = q1("SELECT question, answer FROM approved_answers WHERE id = %s", (id,))
            if row:
                vec = llm.embed(row["question"] + " " + row["answer"])
            if vec:
                exec_("UPDATE approved_answers SET embedding = %s::vector WHERE id = %s", (str(vec), id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s || ' answer', question FROM approved_answers WHERE id = %s",
              (actor_name(), status, id))
        return True

    @strawberry.mutation
    def set_answer_channels(self, id: int, channels: list[str]) -> bool:
        exec_("UPDATE approved_answers SET channels = %s WHERE id = %s", (channels, id))
        return True

    # ——— decisions ———
    @strawberry.mutation
    def add_decision(self, statement: str, context: str = "", source_label: str = "") -> bool:
        exec_("""INSERT INTO decisions (statement, context, status, source_label, owners)
                 VALUES (%s, %s, 'proposed', %s, %s) ON CONFLICT (statement) DO NOTHING""",
              (statement, context, source_label or "Captured in Mari", [actor_name()]))
        audit("captured decision", statement)
        return True

    @strawberry.mutation
    def ratify_decision(self, id: int) -> bool:
        exec_("UPDATE decisions SET status = 'ratified', decided_on = now() WHERE id = %s", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'ratified decision', statement FROM decisions WHERE id = %s",
              (actor_name(), id))
        return True

    @strawberry.mutation
    def supersede_decision(self, id: int, by_statement: str) -> bool:
        exec_("""INSERT INTO decisions (statement, status, source_label, owners, decided_on)
                 VALUES (%s, 'ratified', 'Supersedes an earlier decision', %s, now())
                 ON CONFLICT (statement) DO NOTHING""", (by_statement, [actor_name()]))
        exec_("""UPDATE decisions SET status = 'superseded',
                   superseded_by = (SELECT id FROM decisions WHERE statement = %s)
                 WHERE id = %s""", (by_statement, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'superseded decision', statement FROM decisions WHERE id = %s",
              (actor_name(), id))
        return True

    @strawberry.mutation
    def decision_impact(self, id: int) -> ImpactResult:
        d = q1("SELECT * FROM decisions WHERE id = %s", (id,))
        if not d:
            return ImpactResult(claim="", summary="Decision not found.", docs=[])
        result = MutKnowledge.impact_analysis(self, d["statement"])
        exec_("UPDATE decisions SET impact_summary = %s, impact_count = %s WHERE id = %s",
              (result.summary[:300], len(result.docs), id))
        return result

    # ——— insights ———
    @strawberry.mutation
    def score_readability(self) -> int:
        """Deterministic readability grades (brand: determinism over vibes)."""
        rows = q("SELECT id, title, body, snippet FROM documents")
        for r in rows:
            text = (r["body"] or r["snippet"] or "")
            words = text.split()
            sentences = max(text.count(".") + text.count("!") + text.count("?"), 1)
            avg_len = len(words) / sentences
            long_words = sum(1 for w in words if len(w) > 12) / max(len(words), 1)
            score = avg_len + long_words * 40
            grade = "A" if score < 14 else "B" if score < 20 else "C"
            note = f"{avg_len:.0f} words/sentence"
            exec_("UPDATE documents SET readability = %s WHERE id = %s", (f"{grade}|{note}", r["id"]))
        audit("scored readability", f"{len(rows)} documents")
        return len(rows)

    @strawberry.mutation
    def harvest_glossary(self) -> int:
        docs = q("SELECT title, snippet, body FROM documents")
        corpus = "\n".join(f"{d['title']}: {d['snippet']} {d['body'][:200]}" for d in docs)
        prompt = (
            f"Corpus:\n{corpus}\n\n"
            'Find terms used inconsistently (spelling/hyphenation/casing variants) or undefined jargon. '
            'JSON: [{"term": "...", "variants": "a · b · c", "definition": "one line"}]. At most 3.'
        )
        out = llm.generate_json(prompt, system="You harvest glossary terms from a documentation corpus.")
        added = 0
        if isinstance(out, list):
            for t in out[:3]:
                if not (isinstance(t, dict) and t.get("term")):
                    continue
                term = str(t["term"])[:80]
                # Provenance, established here rather than asked of the model:
                # the document that actually contains the term. A term no
                # document contains was invented, so it is dropped — the review
                # step must be able to open the source it came from.
                doc = q1("""SELECT id, title FROM documents
                            WHERE title ILIKE %s OR body ILIKE %s ORDER BY id LIMIT 1""",
                         (f"%{term}%", f"%{term}%"))
                if not doc:
                    continue
                exec_("""INSERT INTO glossary (term, definition, owner_name, updated, candidate,
                                               variants, evidence, evidence_doc_id)
                         VALUES (%s, %s, 'Mari (harvest)', now(), true, %s, %s, %s)
                         ON CONFLICT (term) DO NOTHING""",
                      (term, str(t.get("definition", ""))[:300], str(t.get("variants", ""))[:200],
                       doc["title"], doc["id"]))
                added += 1
        audit("harvested glossary terms", f"{added} candidates")
        return added

    @strawberry.mutation
    def promote_glossary_candidate(self, id: int, accept: bool) -> bool:
        if accept:
            exec_("UPDATE glossary SET candidate = false WHERE id = %s", (id,))
            exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'accepted glossary term', term FROM glossary WHERE id = %s", (actor_name(), id))
        else:
            exec_("DELETE FROM glossary WHERE id = %s AND candidate", (id,))
        return True

    @strawberry.mutation
    def scan_decisions(self) -> int:
        """Mine recent documents for decisions, inserted as 'proposed' for human
        ratification. Answers with how many were added.

        Prefer `startDecisionScan`, which runs the same scan as a background run
        with a progress reading and a history. This one is bounded — concurrent
        calls under a wall-clock deadline — but it is still model work on a
        request thread, and a slow model still makes it a slow request."""
        return scan_decisions_for()[0]

    @strawberry.mutation
    def scan_facts(self) -> int:
        """Mine recent documents for checkable claims. Answers with how many
        were added. Prefer `startFactScan` — see scan_decisions."""
        return scan_facts_for()[0]

    @strawberry.mutation
    def start_fact_scan(self) -> int:
        """Start the fact scan as a real background run and answer with the run
        id, so the page that asked can follow it. The scan reads the whole
        recent corpus through a model: it is a flow with steps and a history
        like every other long job here, not something a link fires and forgets.
        """
        wf_id = flowengine.ensure_fact_scan_flow()
        n = (q1("SELECT coalesce(max(number), 1800) AS n FROM workflow_runs WHERE workflow_id = %s",
                (wf_id,)) or {"n": 1800})["n"] + 1
        exec_("""INSERT INTO workflow_runs (workflow_id, number, status, started_label, duration,
                                            progress, stats, rows_data)
                 VALUES (%s, %s, 'running', to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, '{}', '[]')""",
              (wf_id, n))
        run = q1("SELECT id FROM workflow_runs WHERE workflow_id = %s AND number = %s", (wf_id, n))
        exec_("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
              (actor_name(), f"started run #{n}", flowengine.FACT_SCAN_FLOW))
        flowengine.start_run(run["id"])
        return run["id"]

    @strawberry.mutation
    def start_decision_scan(self) -> int:
        """Start the decision scan as a real background run and answer with the
        run id, for the same reason the fact scan does: it reads the corpus
        through a model and writes to the ledger, so it belongs in the run
        history rather than behind a link that fires and forgets."""
        wf_id = flowengine.ensure_decision_scan_flow()
        n = (q1("SELECT coalesce(max(number), 1800) AS n FROM workflow_runs WHERE workflow_id = %s",
                (wf_id,)) or {"n": 1800})["n"] + 1
        exec_("""INSERT INTO workflow_runs (workflow_id, number, status, started_label, duration,
                                            progress, stats, rows_data)
                 VALUES (%s, %s, 'running', to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, '{}', '[]')""",
              (wf_id, n))
        run = q1("SELECT id FROM workflow_runs WHERE workflow_id = %s AND number = %s", (wf_id, n))
        exec_("INSERT INTO events (actor, verb, target) VALUES (%s, %s, %s)",
              (actor_name(), f"started run #{n}", flowengine.DECISION_SCAN_FLOW))
        flowengine.start_run(run["id"])
        return run["id"]

    @strawberry.mutation
    def scan_answer_candidates(self, sources: list[str]) -> list[AnswerCandidate]:
        """LLM mines documents ('slack'/'docs') and recent chat questions ('chat')
        for FAQ answer candidates. Inserts NOTHING — the wizard's review step
        decides what becomes an approved answer."""
        existing = {r["question"].lower() for r in q("SELECT question FROM approved_answers")}
        candidates: list[AnswerCandidate] = []

        def collect(out, default_label: str) -> None:
            if not isinstance(out, list):
                return
            for c in out[:4]:
                if not isinstance(c, dict):
                    continue
                question = str(c.get("question", "")).strip()[:200]
                if not question or question.lower() in existing:
                    continue
                existing.add(question.lower())
                confidence = str(c.get("confidence", "medium")).lower()
                candidates.append(AnswerCandidate(
                    question=question,
                    draft_answer=str(c.get("draft_answer", ""))[:1000],
                    source_label=str(c.get("source_label") or default_label)[:120],
                    confidence=confidence if confidence in ("high", "medium", "low") else "medium"))

        def doc_corpus(where: str) -> str:
            docs = q(f"""SELECT title, snippet, body, source FROM documents {where}
                         ORDER BY updated_src DESC NULLS LAST LIMIT 8""")
            return "\n".join(f"[{d['source']}] {d['title']}: {(d['body'] or d['snippet'])[:300]}" for d in docs)

        for src in ("slack", "docs"):
            if src not in sources:
                continue
            corpus = doc_corpus("WHERE source = 'slack'" if src == "slack" else "WHERE source <> 'slack'")
            if not corpus:
                continue
            prompt = (
                f"Recent team documents:\n{corpus}\n\n"
                "Extract QUESTIONS a customer or teammate would plausibly ask that these documents answer. "
                "For each, draft a concise answer grounded in the text. "
                'JSON: [{"question": "...", "draft_answer": "...", "source_label": "which doc it came from", '
                '"confidence": "high|medium|low"}]. At most 4.'
            )
            collect(llm.generate_json(prompt, system="You mine team knowledge for FAQ answer candidates."),
                    f"Mari scan · {src}")

        if "chat" in sources:
            msgs = q("SELECT content FROM chat_messages WHERE role = 'user' ORDER BY id DESC LIMIT 10")
            if msgs:
                corpus = doc_corpus("")
                asked = "\n".join(f"- {m['content'][:200]}" for m in msgs)
                prompt = (
                    f"Questions users recently asked in chat:\n{asked}\n\n"
                    f"Team documents (for grounding answers):\n{corpus}\n\n"
                    "Cluster the asked questions into candidate FAQ questions and draft a concise "
                    "answer for each, grounded in the documents. "
                    'JSON: [{"question": "...", "draft_answer": "...", "source_label": "chat history", '
                    '"confidence": "high|medium|low"}]. At most 4.'
                )
                collect(llm.generate_json(prompt, system="You cluster user questions into FAQ answer candidates."),
                        "Mari scan · chat")

        audit("scanned for answer candidates", f"{len(candidates)} candidates")
        return candidates

    # ——— workflow triggers (init.sql: workflows.trigger, workflow_runs.started_at) ———
    @strawberry.mutation
    def set_workflow_trigger(self, workflow_id: int, trigger: str) -> bool:
        """Set a workflow's trigger. `trigger` is a JSON string — either a
        document trigger {"on": "document_changed"|"document_added"|"",
        "source_id": int|null, "tag": str|null, "path_glob": str|null} or a
        schedule {"on": "schedule", "every_minutes": 1..10080}. Empty "on"
        (or "{}") = manual-only."""
        try:
            trig = json.loads(trigger or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"trigger is not valid JSON: {e}") from e
        if not isinstance(trig, dict):
            raise ValueError("trigger must be a JSON object")
        on = trig.get("on") or ""
        if on not in ("", "document_changed", "document_added", "schedule"):
            raise ValueError("trigger.on must be 'schedule', 'document_changed', 'document_added', or ''")
        if on == "schedule":
            unknown = set(trig) - {"on", "every_minutes"}
            if unknown:
                raise ValueError(f"unknown trigger keys: {sorted(unknown)}")
            try:
                every = int(trig.get("every_minutes"))
            except (TypeError, ValueError) as e:
                raise ValueError("trigger.every_minutes must be an integer") from e
            if not 1 <= every <= 10080:
                raise ValueError("trigger.every_minutes must be between 1 and 10080 (one week)")
            clean = {"on": on, "every_minutes": every}
        else:
            unknown = set(trig) - {"on", "source_id", "tag", "path_glob"}
            if unknown:
                raise ValueError(f"unknown trigger keys: {sorted(unknown)}")
            clean = {"on": on, "source_id": trig.get("source_id"),
                     "tag": trig.get("tag"), "path_glob": trig.get("path_glob")}
        if not q1("SELECT id FROM workflows WHERE id = %s", (workflow_id,)):
            return False
        exec_("UPDATE workflows SET trigger = %s WHERE id = %s", (json.dumps(clean), workflow_id))
        audit("set flow trigger", f"workflow #{workflow_id} → {on or 'manual-only'}")
        return True

    # ——— notifications / watches ———
    @strawberry.mutation
    def mark_notifications_read(self) -> bool:
        exec_("UPDATE notifications SET read = true WHERE user_name = %s", (actor_name(),))
        return True

    @strawberry.mutation
    def toggle_watch(self, document_id: int) -> bool:
        if q1("SELECT 1 AS x FROM watches WHERE user_name = %s AND document_id = %s", (actor_name(), document_id)):
            exec_("DELETE FROM watches WHERE user_name = %s AND document_id = %s", (actor_name(), document_id))
            return False
        exec_("INSERT INTO watches (user_name, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (actor_name(), document_id))
        return True

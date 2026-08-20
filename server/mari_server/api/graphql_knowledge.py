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

from mari_server.api import access
from mari_server.services import workflow_runtime as flowengine
from mari_server.integrations import llm
from mari_server.repositories import lineage_repository as links
from mari_server.services import review as review_application
from mari_server.domain.review import ReviewRecord
from mari_server.repositories import review_repository
from mari_server.repositories import knowledge as knowledge_store
from mari_server.repositories.database import actor_name, audit, exec_, jload, q, q1, transaction
from mari_server.api.graphql_types import AnswerCandidate, ImpactDoc, ImpactResult, ReviewPolicyDecision
from mari_server.services.search import hybrid_search, like_pattern
from mari_components import KnowledgeDocument
from mari_components.knowledge import (
    assess_impact as component_assess_impact,
    check_claims as component_check_claims,
    extract_decisions as component_extract_decisions,
    extract_facts as component_extract_facts,
    harvest_glossary as component_harvest_glossary,
    mine_answers as component_mine_answers,
    refine_document as component_refine_document,
    summarize_digest as component_summarize_digest,
)


# Knowledge operations are implemented by the service layer.
from mari_server.services.knowledge import (
    _SEVERITIES, _TEMPLATE_ICONS, _TONES, _component_document, _iso_date, _slug,
    derive_links as derive_knowledge_links, fact_check_document, is_claim, llm_refine,
    regenerate_digest as regenerate_knowledge_digest, scan_decisions_for, scan_facts_for,
)

@strawberry.type
class MutKnowledge:
    @strawberry.mutation
    def evaluate_review_item(self, review_id: str, dry_run: bool = True) -> ReviewPolicyDecision:
        item = review_repository.find_item(review_id)
        if not item:
            raise ValueError("Review item not found")
        reviewer = actor_name()

        def permitted(actor: str, candidate: ReviewRecord) -> bool:
            member = q1("SELECT role FROM users WHERE name = %s AND status = 'active'", (actor,))
            return bool(member and (member["role"] in ("admin", "manager") or
                                    candidate.assignee.casefold() == actor.casefold()))

        result = review_application.decide(
            item, reviewer, review_repository.ports(), dry_run=dry_run, permission=permitted,
        )
        return ReviewPolicyDecision(
            result.review_id, result.outcome, result.explanation, result.policy_version,
            result.replayed, result.dry_run,
        )

    @strawberry.mutation
    def automate_review(self, first: int = 100, dry_run: bool = True) -> list[ReviewPolicyDecision]:
        """Evaluate a bounded batch; only deterministic `allow` rows mutate."""
        reviewer = actor_name()
        rows = review_application.filter_items(
            review_repository.project_items(), statuses=["pending"],
        )[:min(max(first, 1), 100)]

        def permitted(actor: str, candidate: ReviewRecord) -> bool:
            member = q1("SELECT role FROM users WHERE name = %s AND status = 'active'", (actor,))
            return bool(member and member["role"] in ("admin", "manager"))

        results = [review_application.decide(
            item, reviewer, review_repository.ports(), dry_run=dry_run, permission=permitted,
        ) for item in rows]
        return [ReviewPolicyDecision(
            result.review_id, result.outcome, result.explanation, result.policy_version,
            result.replayed, result.dry_run,
        ) for result in results]

    @strawberry.mutation
    def set_task_done(self, id: int, done: bool) -> bool:
        title = knowledge_store.set_task_done(id, done)
        if not title:
            return False
        audit("completed" if done else "reopened", title)
        return True

    @strawberry.mutation
    def clear_done_tasks(self) -> int:
        n = knowledge_store.clear_done_tasks()
        if n:
            audit("cleared done tasks", f"{n} tasks")
        return n

    @strawberry.mutation
    def verify_fact(self, id: int) -> bool:
        # verified_at is a DATE; the legacy `verified` text column held a
        # display string the console could neither re-format nor sort on.
        claim = knowledge_store.verify_fact(id)
        if not claim:
            return False
        audit("verified fact", claim)
        return True

    @strawberry.mutation
    def create_task(self, title: str, kind: str = "factcheck", kind_label: str = "Fact check",
                    assignee: str = "", due: str | None = None,
                    subject_type: str = "", subject_id: str = "",
                    subject_title: str = "", subject_href: str = "") -> bool:
        """`due` is an ISO date (YYYY-MM-DD) or null. Null is the default:
        nothing in the product assigns a deadline on its own, so a task only
        has one when whoever created it said so.

        `assignee` defaults to unassigned for the same reason — the product
        does not know who should do this, and naming a person nobody chose put
        one developer's name on tasks in every workspace that installed it.

        Subject fields are optional denormalized references. They intentionally
        carry no foreign key: Review can point at documents, facts, decisions,
        runs, or integration-owned objects without coupling this queue to each
        subject table."""
        assignee = assignee.strip()
        initials = "".join(w[0].upper() for w in assignee.split()[:2])
        subject = tuple((value or "").strip() for value in
                        (subject_type, subject_id, subject_title, subject_href))
        due_date = _iso_date(due)
        knowledge_store.create_task(
            title=title, assignee=assignee, initials=initials, kind=kind,
            kind_label=kind_label, due_date=due_date, subject=subject,
        )
        detail = [("Assignee", assignee or "(unassigned)"), ("Kind", kind_label),
                  ("Due", due_date or "(no deadline)")]
        if subject[0]:
            detail.extend([("Subject type", subject[0]), ("Subject", subject[2] or subject[1])])
        audit("created task", title, detail=detail)
        return True

    @strawberry.mutation
    def set_task_due(self, id: int, due: str | None = None) -> bool:
        """Set or (with null/empty) clear a task's deadline. ISO date in, ISO
        date out — the console formats and sorts on the raw value."""
        value = _iso_date(due)
        before = knowledge_store.set_task_due(id, value)
        if not before:
            return False
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
        if doc_id and not knowledge_store.document_exists(doc_id):
            raise ValueError(f"No document {doc_id} to attribute this claim to")
        owner = owner.strip() or actor_name()
        knowledge_store.add_fact(claim, source, owner, doc_id)
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
        if doc_id and not knowledge_store.document_exists(doc_id):
            raise ValueError(f"No document {doc_id} to cite as evidence")
        knowledge_store.upsert_glossary(
            term_id=id, term=term, definition=definition, owner=actor_name(),
            evidence=evidence, document_id=doc_id,
        )
        if id:
            audit("updated glossary term", term)
        else:
            audit("added glossary term", term, detail=[("Evidence", evidence or "(none)")])
        return True

    @strawberry.mutation
    def delete_glossary(self, id: int) -> bool:
        knowledge_store.delete_glossary(id)
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

    @strawberry.mutation
    def fact_check(self, document_id: int) -> int:
        return fact_check_document(document_id)

    # ——— lineage / semantic links ———
    @strawberry.mutation
    def derive_links(self) -> int:
        return derive_knowledge_links()

    @strawberry.mutation
    def pin_node(self, document_id: int, x: float, y: float) -> bool:
        project_id = access.require_current_access().project_id
        doc = q1("SELECT title FROM documents WHERE project_id = %s AND id = %s",
                 (project_id, document_id))
        if not doc:
            return False
        exec_("""UPDATE documents SET graph_x = %s, graph_y = %s
                 WHERE project_id = %s AND id = %s""", (x, y, project_id, document_id))
        audit("pinned graph node", doc["title"])
        return True

    @strawberry.mutation
    def unpin_node(self, document_id: int) -> bool:
        project_id = access.require_current_access().project_id
        row = q1("SELECT title FROM documents WHERE project_id = %s AND id = %s", (project_id, document_id))
        if not row:
            return False
        exec_("UPDATE documents SET graph_x = NULL, graph_y = NULL WHERE project_id = %s AND id = %s",
              (project_id, document_id))
        audit("unpinned graph node", row["title"])
        return True

    @strawberry.mutation
    def save_graph_view(self, name: str, state: str) -> int:
        if len(state.encode("utf-8")) > 256_000:
            raise ValueError("Graph view state is too large (maximum 256KB).")
        try:
            parsed = json.loads(state)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("Graph view state must be valid JSON.") from None
        if not isinstance(parsed, dict):
            raise ValueError("Graph view state must be a JSON object.")
        project_id = access.require_current_access().project_id
        exec_("""INSERT INTO graph_views (project_id, name, state, created_by)
                 VALUES (%s, %s, %s::jsonb, %s)
                 ON CONFLICT (project_id, name) DO UPDATE SET state = EXCLUDED.state""",
              (project_id, name, json.dumps(parsed), actor_name()))
        audit("saved graph view", name)
        return (q1("SELECT id FROM graph_views WHERE project_id = %s AND name = %s",
                   (project_id, name)) or {"id": 0})["id"]

    @strawberry.mutation
    def delete_graph_view(self, id: int) -> bool:
        project_id = access.require_current_access().project_id
        exec_("DELETE FROM graph_views WHERE project_id = %s AND id = %s", (project_id, id))
        return True

    # ——— digest ———
    @strawberry.mutation
    def regenerate_digest(self) -> bool:
        return regenerate_knowledge_digest()

    # ——— impact analysis ———
    @strawberry.mutation
    def impact_analysis(self, claim: str) -> ImpactResult:
        rows = hybrid_search(claim, 6)
        documents = [KnowledgeDocument(
            str(row.get("id") or row.get("external_id") or index), row["title"],
            row.get("body") or row.get("snippet") or "", metadata={"source": row["source"]},
        ) for index, row in enumerate(rows, 1)]
        result = component_assess_impact(
            claim,
            documents,
            generate_json=lambda prompt, _version: llm.generate_json(
                prompt, system="You analyze the blast radius of a changed fact."),
        )
        audit("ran impact analysis", claim)
        by_id = {document.external_id: row for document, row in zip(documents, rows)}
        return ImpactResult(
            claim=claim,
            summary=result.summary,
            docs=[ImpactDoc(
                title=by_id[identifier]["title"], source=by_id[identifier]["source"],
                severity="review", reason="Evidence-linked impact",
            ) for identifier in result.affected_document_ids[:8]],
        )

    # ——— approved answers ———
    @strawberry.mutation
    def upsert_answer(self, question: str, answer: str, id: int | None = None) -> bool:
        project_id = access.require_current_access().project_id
        if id:
            exec_("""UPDATE approved_answers SET question = %s, answer = %s, updated = now()
                     WHERE project_id = %s AND id = %s""", (question, answer, project_id, id))
        else:
            exec_("""INSERT INTO approved_answers
                     (project_id, question, answer, status, owner_name, updated)
                     VALUES (%s, %s, %s, 'draft', %s, now())
                     ON CONFLICT (project_id, question) DO UPDATE
                       SET answer = EXCLUDED.answer, updated = now()""",
                  (project_id, question, answer, actor_name()))
        audit("drafted answer", question)
        return True

    @strawberry.mutation
    def set_answer_status(self, id: int, status: str) -> bool:
        project_id = access.require_current_access().project_id
        row = q1("""SELECT question, answer FROM approved_answers
                    WHERE project_id = %s AND id = %s""", (project_id, id))
        if not row:
            return False
        exec_("""UPDATE approved_answers SET status = %s, updated = now()
                 WHERE project_id = %s AND id = %s""", (status, project_id, id))
        if status == "approved":
            vec = None
            if row:
                vec = llm.embed(row["question"] + " " + row["answer"])
            if vec:
                exec_("""UPDATE approved_answers SET embedding = %s::vector
                         WHERE project_id = %s AND id = %s""", (str(vec), project_id, id))
        audit(f"{status} answer", row["question"])
        return True

    @strawberry.mutation
    def set_answer_channels(self, id: int, channels: list[str]) -> bool:
        project_id = access.require_current_access().project_id
        row = q1("SELECT question FROM approved_answers WHERE project_id = %s AND id = %s", (project_id, id))
        if not row:
            return False
        exec_("UPDATE approved_answers SET channels = %s WHERE project_id = %s AND id = %s",
              (channels, project_id, id))
        audit("updated answer channels", row["question"], detail=[("Channels", ", ".join(channels) or "(none)")])
        return True

    # ——— decisions ———
    @strawberry.mutation
    def add_decision(self, statement: str, context: str = "", source_label: str = "") -> bool:
        project_id = access.require_current_access().project_id
        exec_("""INSERT INTO decisions (project_id, statement, context, status, source_label, owners)
                 VALUES (%s, %s, %s, 'proposed', %s, %s)
                 ON CONFLICT (project_id, statement) DO NOTHING""",
              (project_id, statement, context, source_label or "Captured in Mari", [actor_name()]))
        audit("captured decision", statement)
        return True

    @strawberry.mutation
    def ratify_decision(self, id: int) -> bool:
        project_id = access.require_current_access().project_id
        decision = q1("SELECT statement FROM decisions WHERE project_id = %s AND id = %s",
                      (project_id, id))
        if not decision:
            return False
        exec_("""UPDATE decisions SET status = 'ratified', decided_on = now()
                 WHERE project_id = %s AND id = %s""", (project_id, id))
        audit("ratified decision", decision["statement"])
        return True

    @strawberry.mutation
    def supersede_decision(self, id: int, by_statement: str) -> bool:
        project_id = access.require_current_access().project_id
        old = q1("SELECT statement FROM decisions WHERE project_id = %s AND id = %s", (project_id, id))
        if not old:
            return False
        exec_("""INSERT INTO decisions (project_id, statement, status, source_label, owners, decided_on)
                 VALUES (%s, %s, 'ratified', 'Supersedes an earlier decision', %s, now())
                 ON CONFLICT (project_id, statement) DO NOTHING""", (project_id, by_statement, [actor_name()]))
        exec_("""UPDATE decisions SET status = 'superseded',
                   superseded_by = (SELECT id FROM decisions WHERE project_id = %s AND statement = %s)
                 WHERE project_id = %s AND id = %s""", (project_id, by_statement, project_id, id))
        audit("superseded decision", old["statement"], detail=[("Replacement", by_statement)])
        return True

    @strawberry.mutation
    def decision_impact(self, id: int) -> ImpactResult:
        project_id = access.require_current_access().project_id
        d = q1("SELECT * FROM decisions WHERE project_id = %s AND id = %s", (project_id, id))
        if not d:
            return ImpactResult(claim="", summary="Decision not found.", docs=[])
        result = MutKnowledge.impact_analysis(self, d["statement"])
        exec_("""UPDATE decisions SET impact_summary = %s, impact_count = %s
                 WHERE project_id = %s AND id = %s""",
              (result.summary[:300], len(result.docs), project_id, id))
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
        docs = q("SELECT id, title, snippet, body, source, updated_src FROM documents")
        components = [_component_document(doc) for doc in docs]
        candidates = component_harvest_glossary(
            components,
            generate_json=lambda prompt, _version: llm.generate_json(
                prompt, system="You harvest glossary terms from a documentation corpus."),
        )
        added = 0
        by_id = {str(doc["id"]): doc for doc in docs}
        project_id = access.require_current_access().project_id
        for candidate in candidates[:3]:
            evidence = candidate.evidence[0]
            doc = by_id.get(evidence.document_id)
            if not doc:
                continue
            exec_("""INSERT INTO glossary (project_id, term, definition, owner_name, updated, candidate,
                                           variants, evidence, evidence_doc_id)
                     VALUES (%s, %s, %s, 'Mari (harvest)', now(), true, %s, %s, %s)
                     ON CONFLICT (project_id, term) DO NOTHING""",
                  (project_id, candidate.term[:80], candidate.definition[:300],
                   " · ".join(candidate.aliases)[:200], doc["title"], doc["id"]))
            added += 1
        audit("harvested glossary terms", f"{added} candidates")
        return added

    @strawberry.mutation
    def promote_glossary_candidate(self, id: int, accept: bool) -> bool:
        project_id = access.require_current_access().project_id
        term = q1("SELECT term FROM glossary WHERE project_id = %s AND id = %s", (project_id, id))
        if not term:
            return False
        if accept:
            exec_("UPDATE glossary SET candidate = false WHERE project_id = %s AND id = %s",
                  (project_id, id))
            audit("accepted glossary term", term["term"])
        else:
            exec_("DELETE FROM glossary WHERE project_id = %s AND id = %s AND candidate",
                  (project_id, id))
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
        project_id = access.require_current_access().project_id
        run = q1("""INSERT INTO workflow_runs
                    (project_id, workflow_id, number, status, started_label, duration, progress, stats, rows_data)
                    VALUES (%s, %s, nextval('workflow_run_number_seq'), 'running',
                            to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, '{}', '[]')
                    RETURNING id, number""", (project_id, wf_id))
        n = run["number"]
        audit(f"started run #{n}", flowengine.FACT_SCAN_FLOW)
        flowengine.start_run(run["id"])
        return run["id"]

    @strawberry.mutation
    def start_decision_scan(self) -> int:
        """Start the decision scan as a real background run and answer with the
        run id, for the same reason the fact scan does: it reads the corpus
        through a model and writes to the ledger, so it belongs in the run
        history rather than behind a link that fires and forgets."""
        wf_id = flowengine.ensure_decision_scan_flow()
        project_id = access.require_current_access().project_id
        run = q1("""INSERT INTO workflow_runs
                    (project_id, workflow_id, number, status, started_label, duration, progress, stats, rows_data)
                    VALUES (%s, %s, nextval('workflow_run_number_seq'), 'running',
                            to_char(now(), 'Mon DD, HH12:MI AM'), '00:00:00', 0, '{}', '[]')
                    RETURNING id, number""", (project_id, wf_id))
        n = run["number"]
        audit(f"started run #{n}", flowengine.DECISION_SCAN_FLOW)
        flowengine.start_run(run["id"])
        return run["id"]

    @strawberry.mutation
    def scan_answer_candidates(self, sources: list[str]) -> list[AnswerCandidate]:
        """Mine selected connector documents and optional recent chat questions
        for FAQ answer candidates. Inserts NOTHING — the wizard's review step
        decides what becomes an approved answer."""
        existing = {r["question"].lower() for r in q("SELECT question FROM approved_answers")}
        from mari_components.connectors import CONNECTOR_CATALOG

        selected = sorted(set(sources) & set(CONNECTOR_CATALOG))
        docs = q("""SELECT id, title, snippet, body, source, updated_src FROM documents
                     WHERE source = ANY(%s) ORDER BY updated_src DESC NULLS LAST LIMIT 16""",
                 (selected,)) if selected else []
        components = [_component_document(doc) for doc in docs]
        if "chat" in sources:
            for index, message in enumerate(q(
                    "SELECT content FROM chat_messages WHERE role = 'user' ORDER BY id DESC LIMIT 10"), 1):
                components.append(KnowledgeDocument(
                    f"chat:{index}", "Recent user question", str(message["content"])[:200],
                    revision="recent-chat"))
        mined = component_mine_answers(
            components,
            generate_json=lambda prompt, _version: llm.generate_json(
                prompt, system="You mine team knowledge for FAQ answer candidates."),
        ) if components else ()
        candidates: list[AnswerCandidate] = []
        titles = {str(doc["id"]): doc["title"] for doc in docs}
        for candidate in mined[:8]:
            if candidate.question.casefold() in existing:
                continue
            existing.add(candidate.question.casefold())
            source_label = titles.get(candidate.evidence[0].document_id, "Recent chat")
            confidence = "high" if candidate.confidence >= .85 else "medium" if candidate.confidence >= .6 else "low"
            candidates.append(AnswerCandidate(
                question=candidate.question[:200], draft_answer=candidate.answer[:1000],
                source_label=source_label[:120], confidence=confidence))

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

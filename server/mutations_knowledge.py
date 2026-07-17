"""Mari Cloud — knowledge mutations: docs, facts, decisions, answers, glossary,
tags, tasks, lineage, digest, insights, watches."""

from __future__ import annotations

import json

import strawberry

import llm
from db import ME, audit, exec_, jload, q, q1
from gqltypes import AnswerCandidate, ImpactDoc, ImpactResult
from queries import hybrid_search

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


@strawberry.type
class MutKnowledge:
    @strawberry.mutation
    def set_task_done(self, id: int, done: bool) -> bool:
        exec_("UPDATE tasks SET done = %s WHERE id = %s", (done, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s, title FROM tasks WHERE id = %s",
              (ME, "completed" if done else "reopened", id))
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
        exec_("UPDATE facts SET status = 'Verified', verified = to_char(now(), 'Mon DD, YYYY') WHERE id = %s", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'verified fact', claim FROM facts WHERE id = %s", (ME, id))
        return True

    @strawberry.mutation
    def create_task(self, title: str, kind: str = "factcheck", kind_label: str = "Fact check",
                    assignee: str = "Daniel H.") -> bool:
        initials = "".join(w[0].upper() for w in assignee.split()[:2]) or "DH"
        exec_("""INSERT INTO tasks (title, assignee, assignee_initials, assignee_tint, kind, kind_label)
                 VALUES (%s, %s, %s, 1, %s, %s) ON CONFLICT (title) DO NOTHING""",
              (title, assignee, initials, kind, kind_label))
        audit("created task", title)
        return True

    @strawberry.mutation
    def add_fact(self, claim: str, source: str, owner: str = "Daniel H.") -> bool:
        exec_("""INSERT INTO facts (claim, source, owner_name, owner_tint, status, verified)
                 VALUES (%s, %s, %s, 1, 'Needs review', '—') ON CONFLICT (claim) DO NOTHING""",
              (claim, source, owner))
        audit("added fact", claim)
        return True

    # ——— glossary CRUD ———
    @strawberry.mutation
    def upsert_glossary(self, term: str, definition: str, id: int | None = None) -> bool:
        if id:
            exec_("UPDATE glossary SET term = %s, definition = %s, updated = now() WHERE id = %s", (term, definition, id))
            audit("updated glossary term", term)
        else:
            exec_("""INSERT INTO glossary (term, definition, owner_name, updated) VALUES (%s, %s, %s, now())
                     ON CONFLICT (term) DO UPDATE SET definition = EXCLUDED.definition, updated = now()""",
                  (term, definition, ME))
            audit("added glossary term", term)
        return True

    @strawberry.mutation
    def delete_glossary(self, id: int) -> bool:
        exec_("DELETE FROM glossary WHERE id = %s", (id,))
        audit("deleted glossary term", f"#{id}")
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
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'edited', title FROM documents WHERE id = %s", (ME, id))
        return True

    @strawberry.mutation
    def set_change_status(self, id: int, status: str) -> bool:
        exec_("UPDATE changes SET status = %s WHERE id = %s", (status, id))
        if status == "accepted":
            exec_("""UPDATE documents d SET body = replace(d.body, c.original, c.replacement)
                     FROM changes c WHERE c.id = %s AND d.id = c.document_id""", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, %s || ' change', original FROM changes WHERE id = %s",
              (ME, status, id))
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
              (ME, document_id))
        return True

    @strawberry.mutation
    def unpin_node(self, document_id: int) -> bool:
        exec_("UPDATE documents SET graph_x = NULL, graph_y = NULL WHERE id = %s", (document_id,))
        return True

    @strawberry.mutation
    def save_graph_view(self, name: str, state: str) -> int:
        exec_("""INSERT INTO graph_views (name, state, created_by) VALUES (%s, %s::jsonb, %s)
                 ON CONFLICT (name) DO UPDATE SET state = EXCLUDED.state""", (name, state, ME))
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
                  (question, answer, ME))
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
              (ME, status, id))
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
              (statement, context, source_label or "Captured in Mari", [ME]))
        audit("captured decision", statement)
        return True

    @strawberry.mutation
    def ratify_decision(self, id: int) -> bool:
        exec_("UPDATE decisions SET status = 'ratified', decided_on = now() WHERE id = %s", (id,))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'ratified decision', statement FROM decisions WHERE id = %s",
              (ME, id))
        return True

    @strawberry.mutation
    def supersede_decision(self, id: int, by_statement: str) -> bool:
        exec_("""INSERT INTO decisions (statement, status, source_label, owners, decided_on)
                 VALUES (%s, 'ratified', 'Supersedes an earlier decision', %s, now())
                 ON CONFLICT (statement) DO NOTHING""", (by_statement, [ME]))
        exec_("""UPDATE decisions SET status = 'superseded',
                   superseded_by = (SELECT id FROM decisions WHERE statement = %s)
                 WHERE id = %s""", (by_statement, id))
        exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'superseded decision', statement FROM decisions WHERE id = %s",
              (ME, id))
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
                if isinstance(t, dict) and t.get("term"):
                    exec_("""INSERT INTO glossary (term, definition, owner_name, updated, candidate, variants)
                             VALUES (%s, %s, 'Mari (harvest)', now(), true, %s) ON CONFLICT (term) DO NOTHING""",
                          (str(t["term"])[:80], str(t.get("definition", ""))[:300], str(t.get("variants", ""))[:200]))
                    added += 1
        audit("harvested glossary terms", f"{added} candidates")
        return added

    @strawberry.mutation
    def promote_glossary_candidate(self, id: int, accept: bool) -> bool:
        if accept:
            exec_("UPDATE glossary SET candidate = false WHERE id = %s", (id,))
            exec_("INSERT INTO events (actor, verb, target) SELECT %s, 'accepted glossary term', term FROM glossary WHERE id = %s", (ME, id))
        else:
            exec_("DELETE FROM glossary WHERE id = %s AND candidate", (id,))
        return True

    # ——— LLM scanners: mine the doc graph (recency-ordered) for candidates ———
    @strawberry.mutation
    def scan_decisions(self) -> int:
        """LLM reads recent documents (+ their graph neighbors) and proposes
        decision candidates, inserted as 'proposed' for human ratification."""
        docs = q("""SELECT d.title, d.snippet, d.body, d.source, d.updated_src FROM documents d
                    ORDER BY d.updated_src DESC NULLS LAST LIMIT 8""")
        existing = {r["statement"] for r in q("SELECT statement FROM decisions")}
        corpus = "\n".join(f"[{d['source']} · {d['updated_src']}] {d['title']}: {(d['body'] or d['snippet'])[:300]}"
                            for d in docs)
        prompt = (
            f"Recent team documents (newest first):\n{corpus}\n\n"
            "Extract DECISIONS the team appears to have made (things chosen, changed, or committed to). "
            'JSON: [{"statement": "declarative one-liner", "context": "1 sentence of why", '
            '"source_label": "which doc it came from"}]. At most 3; skip anything already obvious policy.'
        )
        out = llm.generate_json(prompt, system="You mine team knowledge for decisions worth ratifying.")
        added = 0
        if isinstance(out, list):
            for d in out[:3]:
                stmt = str(d.get("statement", ""))[:200]
                if stmt and stmt not in existing:
                    exec_("""INSERT INTO decisions (statement, context, status, source_label, owners)
                             VALUES (%s, %s, 'proposed', %s, %s) ON CONFLICT (statement) DO NOTHING""",
                          (stmt, str(d.get("context", ""))[:400],
                           ("Mari scan · " + str(d.get("source_label", "doc graph")))[:120], [ME]))
                    added += 1
        audit("scanned for decisions", f"{added} candidates")
        return added

    @strawberry.mutation
    def scan_facts(self) -> int:
        """LLM extracts atomic, checkable claims from recent documents; lands
        as 'Needs review' facts for verification."""
        docs = q("""SELECT title, snippet, body, source FROM documents
                    ORDER BY updated_src DESC NULLS LAST LIMIT 8""")
        existing = {r["claim"].lower() for r in q("SELECT claim FROM facts")}
        corpus = "\n".join(f"[{d['source']}] {d['title']}: {(d['body'] or d['snippet'])[:300]}" for d in docs)
        prompt = (
            f"Recent team documents:\n{corpus}\n\n"
            "Extract atomic, checkable FACTS (numbers, limits, defaults, policies). "
            'JSON: [{"claim": "one factual sentence", "source": "doc title"}]. At most 4; no opinions.'
        )
        out = llm.generate_json(prompt, system="You extract verifiable facts from documentation.")
        added = 0
        if isinstance(out, list):
            for f in out[:4]:
                claim = str(f.get("claim", ""))[:200]
                if claim and claim.lower() not in existing:
                    exec_("""INSERT INTO facts (claim, source, owner_name, owner_tint, status, verified)
                             VALUES (%s, %s, %s, 1, 'Needs review', '—') ON CONFLICT (claim) DO NOTHING""",
                          (claim, ("Mari scan · " + str(f.get("source", "doc graph")))[:80], ME))
                    added += 1
        audit("scanned for facts", f"{added} candidates")
        return added

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
        exec_("UPDATE notifications SET read = true WHERE user_name = %s", (ME,))
        return True

    @strawberry.mutation
    def toggle_watch(self, document_id: int) -> bool:
        if q1("SELECT 1 AS x FROM watches WHERE user_name = %s AND document_id = %s", (ME, document_id)):
            exec_("DELETE FROM watches WHERE user_name = %s AND document_id = %s", (ME, document_id))
            return False
        exec_("INSERT INTO watches (user_name, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (ME, document_id))
        return True

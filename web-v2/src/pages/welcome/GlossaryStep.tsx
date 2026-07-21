// Glossary seeding step — POST /onboard/glossary-harvest proposes candidates
// grounded in real documents (nothing persisted server-side); accepted rows go
// through the same upsertGlossary mutation the Library glossary uses, one call
// per term with a live progress count. Skippable; the llm:false fallback is
// flagged honestly.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Chip, EmptyState, Spinner } from "../../components/ui";
import { gql } from "../../lib/api";

type Candidate = { term: string; definition: string; evidence: string };
type Harvest = { candidates: Candidate[]; llm: boolean };

export function GlossaryStep({ sourceId, added, onAdded }: {
  /** Harvest against the source connected this session, or all docs when null. */
  sourceId: number | null;
  added: number;
  onAdded: (n: number) => void;
}) {
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [harvest, setHarvest] = useState<Harvest | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<{ done: number; total: number } | null>(null);
  const [addResult, setAddResult] = useState<{ ok: number; failed: number } | null>(null);

  const scan = async () => {
    setScanning(true);
    setScanError("");
    setAddResult(null);
    try {
      const r = await fetch("/onboard/glossary-harvest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sourceId != null ? { sourceId, limit: 15 } : { limit: 15 }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || !Array.isArray(j.candidates)) {
        setScanError(typeof j?.detail === "string" ? j.detail : `Scan failed — the server answered ${r.status}.`);
      } else {
        setHarvest(j as Harvest);
        setChecked(new Set((j.candidates as Candidate[]).map((c) => c.term)));
      }
    } catch {
      setScanError("Scan failed — the API didn’t answer.");
    }
    setScanning(false);
  };

  const toggle = (term: string) =>
    setChecked((s) => { const n = new Set(s); if (!n.delete(term)) n.add(term); return n; });

  const addTerms = async () => {
    if (!harvest || adding) return;
    const picked = harvest.candidates.filter((c) => checked.has(c.term));
    setAdding({ done: 0, total: picked.length });
    let ok = 0;
    let failed = 0;
    for (const [i, c] of picked.entries()) {
      const r = await gql<{ upsertGlossary: boolean }>(
        `mutation($term: String!, $definition: String!) { upsertGlossary(term: $term, definition: $definition) }`,
        { term: c.term, definition: c.definition },
      );
      if (r?.upsertGlossary) ok += 1; else failed += 1;
      setAdding({ done: i + 1, total: picked.length });
    }
    setAdding(null);
    setAddResult({ ok, failed });
    setHarvest(null);
    onAdded(added + ok);
  };

  if (scanning) {
    return <div className="wc-center wc-scanning"><Spinner size="sm" /> Reading your docs… (the LLM pass can take a minute)</div>;
  }

  if (adding) {
    return <div className="wc-center wc-scanning"><Spinner size="sm" /> Adding term {adding.done} of {adding.total}…</div>;
  }

  if (harvest) {
    if (harvest.candidates.length === 0) {
      return (
        <EmptyState
          icon={<Ic.Book size={22} />}
          title="Nothing new to suggest"
          action={<Button compact onClick={scan}><Ic.Refresh size={13} /> Scan again</Button>}
        >
          Every candidate the scan found is either already in the glossary or didn’t pass grounding checks.
        </EmptyState>
      );
    }
    return (
      <>
        {!harvest.llm && (
          <div className="wc-note" role="note">
            <Ic.Sparkle size={14} />
            <span><b>LLM unavailable</b> — these candidates came from a deterministic text scan (frequent capitalized phrases), not the language model. Review them extra carefully.</span>
          </div>
        )}
        <div className="wc-cands" role="group" aria-label="Glossary candidates">
          {harvest.candidates.map((c) => (
            <label key={c.term} className={`wc-cand${checked.has(c.term) ? " wc-cand--on" : ""}`}>
              <input type="checkbox" checked={checked.has(c.term)} onChange={() => toggle(c.term)} />
              <span className="wc-cand__body">
                <span className="wc-cand__top">
                  <b>{c.term}</b>
                  <Chip tone="faint" icon={<Ic.Doc size={11} />} title={`Found in “${c.evidence}”`}>{c.evidence}</Chip>
                </span>
                <span className="wc-cand__def">{c.definition}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="wc-row wc-cands-actions">
          <Button variant="primary" disabled={checked.size === 0} onClick={addTerms}>
            <Ic.Plus size={13} /> Add {checked.size} term{checked.size === 1 ? "" : "s"}
          </Button>
          <Button onClick={scan}><Ic.Refresh size={13} /> Scan again</Button>
          <span className="card__hint">Unchecked rows are discarded — nothing was saved by the scan itself.</span>
        </div>
      </>
    );
  }

  return (
    <div className="wc-glossary-start">
      {addResult ? (
        <div className="wc-ok">
          <Ic.CheckCircle size={15} /> Added {addResult.ok} term{addResult.ok === 1 ? "" : "s"} to the glossary
          {addResult.failed > 0 && <span className="wc-sum-err"> · {addResult.failed} failed (server rejected the write)</span>}
        </div>
      ) : (
        <p className="wc-drawer-sub">
          Mari reads your most-connected documents{sourceId != null ? " (starting with the source you just connected)" : ""} and
          proposes glossary terms with evidence. Nothing is saved until you review and accept.
        </p>
      )}
      <div className="wc-row">
        <Button variant="primary" onClick={scan}><Ic.Search size={14} /> {addResult ? "Scan again" : "Scan my documents"}</Button>
        {added > 0 && <span className="card__hint">{added} term{added === 1 ? "" : "s"} added this session</span>}
      </div>
      {scanError && <div className="wc-error" role="alert">{scanError}</div>}
    </div>
  );
}

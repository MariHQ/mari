import { useState } from "react";
import * as Ic from "../../components/icons";
import {
  Button,
  Card,
  Chip,
  ChipTone,
  Drawer,
  EmptyState,
  Spinner,
  Stepper,
  Textarea,
} from "../../components/ui";
import { gql } from "../../lib/api";

type SourceId = "slack" | "docs" | "chat";

const SOURCES: { id: SourceId; label: string; description: string; icon: React.ReactNode }[] = [
  {
    id: "slack",
    label: "Slack",
    description: "Questions your team keeps answering in channels and threads.",
    icon: <Ic.Chat size={17} />,
  },
  {
    id: "docs",
    label: "Docs & repos",
    description: "FAQs buried in synced documents, READMEs and PR discussions.",
    icon: <Ic.Doc size={17} />,
  },
  {
    id: "chat",
    label: "Ask-Mari chat history",
    description: "Questions the bot had to generate an answer for — no approved match.",
    icon: <Ic.Sparkle size={17} />,
  },
];

const STEPS = ["Sources", "Scan", "Review", "Import"];

type Candidate = {
  question: string;
  draftAnswer: string;
  sourceLabel: string;
  confidence: number | string;
  accepted: boolean;
};

type ImportStatus = "pending" | "saving" | "done";

/** high ≥ 0.75, med ≥ 0.45, else low; string confidences pass through. */
function confLevel(c: number | string): "high" | "med" | "low" {
  if (typeof c === "string") {
    const l = c.toLowerCase();
    return l.startsWith("high") ? "high" : l.startsWith("med") ? "med" : "low";
  }
  return c >= 0.75 ? "high" : c >= 0.45 ? "med" : "low";
}
function confLabel(c: number | string): string {
  if (typeof c === "number") return `${Math.round(c * 100)}% confident`;
  return c;
}
const CONF_TONE: Record<"high" | "med" | "low", ChipTone> = { high: "green", med: "gold", low: "ink" };

/**
 * "Harvest questions" — LLM question-harvest wizard. Scans the picked
 * sources for question/answer candidates (nothing persists), lets you edit
 * and accept each one, then imports the accepted set as drafts via
 * upsertAnswer. Drafts flow through the normal approve pipeline.
 */
export default function HarvestWizard({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful import — close into the list filtered to Drafts. */
  onImported: (count: number) => void;
}) {
  const [step, setStep] = useState(0);
  const [picked, setPicked] = useState<Set<SourceId>>(new Set(["slack", "docs", "chat"]));
  const [scanning, setScanning] = useState(false);
  const [scanFailed, setScanFailed] = useState(false);
  const [scanEmpty, setScanEmpty] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<Record<number, ImportStatus>>({});
  const [importedCount, setImportedCount] = useState<number | null>(null);

  const reset = () => {
    setStep(0);
    setScanning(false);
    setScanFailed(false);
    setScanEmpty(false);
    setCandidates([]);
    setImporting(false);
    setImportStatus({});
    setImportedCount(null);
  };

  const handleOpenChange = (v: boolean) => {
    if (!v && importing) return; // don't lose an import mid-flight
    onOpenChange(v);
    if (!v) reset();
  };

  const togglePick = (id: SourceId) =>
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const scan = async () => {
    setStep(1);
    setScanning(true);
    setScanFailed(false);
    setScanEmpty(false);
    const sources = SOURCES.filter((s) => picked.has(s.id)).map((s) => `"${s.id}"`);
    const d: any = await gql(
      `mutation { scanAnswerCandidates(sources: [${sources.join(", ")}]) { question draftAnswer sourceLabel confidence } }`,
    );
    setScanning(false);
    const found: any[] | null = d?.scanAnswerCandidates ?? null;
    if (found === null) {
      setScanFailed(true);
      return;
    }
    if (found.length === 0) {
      setScanEmpty(true);
      return;
    }
    setCandidates(
      found.map((c) => ({
        question: c.question ?? "",
        draftAnswer: c.draftAnswer ?? "",
        sourceLabel: c.sourceLabel ?? "",
        confidence: c.confidence ?? 0,
        accepted: true,
      })),
    );
    setStep(2);
  };

  const setAccepted = (i: number, accepted: boolean) =>
    setCandidates((list) => list.map((c, j) => (j === i ? { ...c, accepted } : c)));
  const editDraft = (i: number, draftAnswer: string) =>
    setCandidates((list) => list.map((c, j) => (j === i ? { ...c, draftAnswer } : c)));
  const acceptHighConfidence = () =>
    setCandidates((list) => list.map((c) => ({ ...c, accepted: confLevel(c.confidence) === "high" })));

  const acceptedCount = candidates.filter((c) => c.accepted).length;

  const doImport = async () => {
    if (importing || acceptedCount === 0) return;
    setStep(3);
    setImporting(true);
    const status: Record<number, ImportStatus> = {};
    candidates.forEach((c, i) => { if (c.accepted) status[i] = "pending"; });
    setImportStatus({ ...status });
    let done = 0;
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      if (!c.accepted) continue;
      setImportStatus((s) => ({ ...s, [i]: "saving" }));
      await gql(`mutation($q: String!, $a: String!) { upsertAnswer(question: $q, answer: $a) }`, {
        q: c.question.trim(),
        a: c.draftAnswer.trim(),
      });
      done++;
      setImportStatus((s) => ({ ...s, [i]: "done" }));
    }
    setImporting(false);
    setImportedCount(done);
  };

  const finish = () => {
    const n = importedCount ?? 0;
    reset();
    onImported(n);
  };

  const sourceLine = SOURCES.filter((s) => picked.has(s.id)).map((s) => s.label).join(", ");

  const footer =
    step === 0 ? (
      <>
        <Button onClick={() => handleOpenChange(false)}>Cancel</Button>
        <Button variant="primary" disabled={picked.size === 0} onClick={scan}>
          <Ic.Sparkle size={14} /> Scan {picked.size} source{picked.size === 1 ? "" : "s"}
        </Button>
      </>
    ) : step === 1 ? (
      <Button disabled={scanning} onClick={() => setStep(0)}>
        <Ic.ChevL size={13} /> Back to sources
      </Button>
    ) : step === 2 ? (
      <>
        <Button onClick={() => setStep(0)}><Ic.ChevL size={13} /> Back</Button>
        <Button variant="primary" disabled={acceptedCount === 0} onClick={doImport}>
          <Ic.Check size={14} /> Import {acceptedCount} draft{acceptedCount === 1 ? "" : "s"}
        </Button>
      </>
    ) : importedCount !== null ? (
      <Button variant="primary" onClick={finish}>
        <Ic.CheckCircle size={14} /> View {importedCount} new draft{importedCount === 1 ? "" : "s"}
      </Button>
    ) : null;

  return (
    <Drawer open={open} onOpenChange={handleOpenChange} title="Harvest questions" width={620} footer={footer}>
      <Stepper labels={STEPS} current={step} ariaLabel="Harvest progress" />

      {/* 1 — pick sources */}
      {step === 0 && (
        <div className="hw-sources">
          <p className="hw-lead">
            Mari reads the sources you pick and pulls out questions your team keeps answering —
            nothing is saved until you review and import.
          </p>
          {SOURCES.map((s) => {
            const on = picked.has(s.id);
            return (
              <label key={s.id} className={`hw-src${on ? " hw-src--on" : ""}`}>
                <input type="checkbox" checked={on} onChange={() => togglePick(s.id)} />
                <span className="hw-src__icon">{s.icon}</span>
                <span className="hw-src__copy">
                  <b>{s.label}</b>
                  <span>{s.description}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}

      {/* 2 — scan */}
      {step === 1 && (
        <div className="hw-scan">
          {scanning ? (
            <>
              <Spinner size="md" label="Scanning" />
              <p className="hw-scan__line">Mari is reading recent documents from {sourceLine}…</p>
              <p className="hw-scan__sub">Looking for questions that keep getting answered by hand.</p>
            </>
          ) : scanFailed ? (
            <EmptyState
              icon={<Ic.Sparkle size={20} />}
              title="Scan failed"
              action={<Button compact onClick={scan}><Ic.Refresh size={13} /> Retry scan</Button>}
            >
              Couldn’t reach Mari — is the API running?
            </EmptyState>
          ) : scanEmpty ? (
            <EmptyState
              icon={<Ic.Search size={20} />}
              title="Nothing new found"
              action={<Button compact onClick={scan}><Ic.Refresh size={13} /> Scan again</Button>}
            >
              No new question candidates in {sourceLine} right now.
            </EmptyState>
          ) : null}
        </div>
      )}

      {/* 3 — review */}
      {step === 2 && (
        <div className="hw-review">
          <div className="hw-review__bar">
            <span className="hw-review__count">
              {acceptedCount} of {candidates.length} accepted
            </span>
            <Button compact onClick={acceptHighConfidence}>
              <Ic.Check size={13} /> Accept all high confidence
            </Button>
          </div>
          {candidates.map((c, i) => {
            const level = confLevel(c.confidence);
            return (
              <Card key={i} className={`hw-cand${c.accepted ? "" : " hw-cand--skipped"}`}>
                <h3 className="hw-cand__q">{c.question}</h3>
                <div className="hw-cand__meta">
                  {c.sourceLabel && <Chip icon={<Ic.Doc size={13} />}>{c.sourceLabel}</Chip>}
                  <Chip tone={CONF_TONE[level]} dot>{confLabel(c.confidence)}</Chip>
                </div>
                <Textarea
                  short
                  value={c.draftAnswer}
                  onChange={(e) => editDraft(i, e.target.value)}
                  aria-label={`Draft answer for “${c.question}”`}
                />
                <div className="hw-cand__actions">
                  {c.accepted ? (
                    <>
                      <span className="hw-cand__state hw-cand__state--on"><Ic.CheckCircle size={14} /> Accepted</span>
                      <Button compact onClick={() => setAccepted(i, false)}>Skip</Button>
                    </>
                  ) : (
                    <>
                      <span className="hw-cand__state">Skipped</span>
                      <Button compact variant="success" onClick={() => setAccepted(i, true)}>
                        <Ic.Check size={13} /> Accept
                      </Button>
                    </>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* 4 — import */}
      {step === 3 && (
        <div className="hw-import">
          <p className="hw-lead">
            {importedCount !== null
              ? `${importedCount} draft${importedCount === 1 ? "" : "s"} imported. They flow through the normal approve pipeline — approving embeds them for serving.`
              : "Saving accepted candidates as drafts…"}
          </p>
          <ul className="hw-import__list">
            {candidates.map((c, i) => {
              const st = importStatus[i];
              if (!st) return null;
              return (
                <li key={i} className="hw-import__row">
                  {st === "done" ? (
                    <span className="hw-import__mark hw-import__mark--done"><Ic.CheckCircle size={15} /></span>
                  ) : st === "saving" ? (
                    <span className="hw-import__mark"><Spinner size="sm" label="Saving" /></span>
                  ) : (
                    <span className="hw-import__mark hw-import__mark--pending" />
                  )}
                  <span className="hw-import__q">{c.question}</span>
                  <span className="hw-import__st">{st === "done" ? "draft saved" : st === "saving" ? "saving…" : "queued"}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </Drawer>
  );
}

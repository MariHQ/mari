import { Fragment, useEffect, useState } from "react";
import * as Ic from "../components/icons";
import { ImpactPanel } from "../components/ImpactPanel";
import {
  Avatar,
  Button,
  Card,
  Chip,
  ChipStatus,
  EmptyState,
  Input,
  PageHeader,
  StatusChip,
  Table,
  Tabs,
  fmtDate,
  useToast,
} from "../components/ui";
import { gql } from "../lib/api";

type FactRow = {
  id?: number;
  claim: string;
  source: string;
  owner: string;
  ownerTint: number;
  status: string;
  verified: string;
};

type ImpactDoc = { title: string; source: string; severity: string; reason: string };
type ImpactState = { loading: boolean; open: boolean; summary?: string; docs?: ImpactDoc[] };

const STATUS_CHIP: Record<string, ChipStatus> = {
  Verified: "verified",
  "Needs review": "needs-review",
  Stale: "stale",
};

const FILTERS = ["All", "Verified", "Needs review", "Stale"] as const;
type Filter = (typeof FILTERS)[number];

const FACTS_QUERY = `{ facts { id claim source owner ownerTint status verified } }`;

const STALE_AFTER_DAYS = 60;

/** Audit ages are relative to the newest verification on record (or today),
 *  so mixed seed/live data never renders negative ages. */
const auditAnchor = (rows: FactRow[]): number => {
  const newest = rows.reduce((max, f) => {
    const t = new Date(f.verified).getTime();
    return Number.isNaN(t) ? max : Math.max(max, t);
  }, 0);
  return newest || Date.now();
};

const verifiedAgeDays = (verified: string, anchor: number): number | null => {
  const t = new Date(verified).getTime();
  return Number.isNaN(t) ? null : Math.round((anchor - t) / 86400000);
};

export default function FactsPage() {
  const [rows, setRows] = useState<FactRow[]>([]);
  const [filter, setFilter] = useState<Filter>("All");
  const [composerOpen, setComposerOpen] = useState(false);
  const [newClaim, setNewClaim] = useState("");
  const [newSource, setNewSource] = useState("");
  const [adding, setAdding] = useState(false);
  const [impact, setImpact] = useState<Record<string, ImpactState>>({});
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditTasks, setAuditTasks] = useState<Record<string, "creating" | "done" | "error">>({});
  const [scanning, setScanning] = useState(false);
  const toast = useToast();

  const load = () =>
    gql(FACTS_QUERY).then((d: any) => {
      if (d?.facts) setRows(d.facts);
    });

  useEffect(() => { load(); }, []);

  const verify = async (f: FactRow) => {
    if (f.id == null) return;
    await gql(`mutation($id: Int!) { verifyFact(id: $id) }`, { id: f.id });
    load();
  };

  const addFact = async () => {
    if (!newClaim.trim() || !newSource.trim() || adding) return;
    setAdding(true);
    await gql(`mutation($claim: String!, $source: String!) { addFact(claim: $claim, source: $source) }`, {
      claim: newClaim.trim(), source: newSource.trim(),
    });
    setAdding(false);
    setNewClaim("");
    setNewSource("");
    setComposerOpen(false);
    load();
  };

  const runImpact = async (f: FactRow) => {
    const key = f.claim;
    const existing = impact[key];
    if (existing?.loading) return;
    if (existing?.docs) {
      // already analyzed — just toggle open/closed
      setImpact((m) => ({ ...m, [key]: { ...existing, open: !existing.open } }));
      return;
    }
    setImpact((m) => ({ ...m, [key]: { loading: true, open: true } }));
    const d: any = await gql(
      `mutation($claim: String!) { impactAnalysis(claim: $claim) { claim summary docs { title source severity reason } } }`,
      { claim: f.claim },
    );
    const r = d?.impactAnalysis;
    setImpact((m) => ({
      ...m,
      [key]: r
        ? { loading: false, open: true, summary: r.summary, docs: r.docs ?? [] }
        : { loading: false, open: true, summary: "Analysis unavailable — is the API running?", docs: [] },
    }));
  };

  const createReviewTask = async (f: FactRow) => {
    const st = auditTasks[f.claim];
    if (st === "creating" || st === "done") return;
    setAuditTasks((m) => ({ ...m, [f.claim]: "creating" }));
    const d = await gql(
      `mutation($title: String!, $kind: String!, $kindLabel: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel) }`,
      { title: `Re-verify: ${f.claim}`, kind: "factcheck", kindLabel: "Fact check" },
    );
    setAuditTasks((m) => ({ ...m, [f.claim]: d ? "done" : "error" }));
  };

  const scanForFacts = async () => {
    if (scanning) return;
    setScanning(true);
    const d: any = await gql(`mutation { scanFacts }`);
    setScanning(false);
    if (d == null || typeof d.scanFacts !== "number") {
      toast("Scan failed — couldn’t reach Mari. Is the API running?", "error");
      return;
    }
    const n = d.scanFacts;
    toast(n > 0 ? `${n} new claim${n === 1 ? "" : "s"} found — review below` : "Nothing new found", "success");
    load();
  };

  // client-side audit over the facts query: every Verified fact, oldest first,
  // flagged as a stale candidate once its verification is older than 60 days
  const anchor = auditAnchor(rows);
  const auditRows = rows
    .filter((f) => f.status === "Verified")
    .map((f) => ({ fact: f, age: verifiedAgeDays(f.verified, anchor) }))
    .sort((a, b) => (b.age ?? -1) - (a.age ?? -1));
  const staleCandidates = auditRows.filter((r) => r.age != null && r.age > STALE_AFTER_DAYS);

  const visible = rows.filter((f) => filter === "All" || f.status === filter);
  const countOf = (f: Filter) => (f === "All" ? rows.length : rows.filter((r) => r.status === f).length);

  return (
    <>
      <PageHeader
        title="Facts"
        description="Every claim the team relies on, verified and owned."
        actions={
          <>
            <Button onClick={scanForFacts} disabled={scanning}>
              <Ic.Sparkle size={15} />
              {scanning ? "Mari is reading recent documents…" : "Scan for facts"}
            </Button>
            <Button aria-expanded={auditOpen} onClick={() => setAuditOpen((v) => !v)}>
              <Ic.ShieldCheck size={15} /> {auditOpen ? "Hide audit" : "Audit documents"}
            </Button>
            <Button variant="primary" onClick={() => setComposerOpen((v) => !v)}>
              <Ic.Quill size={15} /> New fact
            </Button>
          </>
        }
      />

      {/* status filter chips */}
      <Tabs
        variant="filter"
        ariaLabel="Filter facts by status"
        value={filter}
        onChange={setFilter}
        options={FILTERS.map((f) => ({ id: f, label: f, count: countOf(f) }))}
      />

      {/* verification audit — inline panel, no navigation */}
      {auditOpen && (
        <Card
          title="Verification audit"
          hint={
            `${auditRows.length} verified fact${auditRows.length === 1 ? "" : "s"} · ` +
            (staleCandidates.length === 0
              ? `none verified more than ${STALE_AFTER_DAYS} days ago`
              : `${staleCandidates.length} verified more than ${STALE_AFTER_DAYS} days ago — stale candidate${staleCandidates.length === 1 ? "" : "s"}`)
          }
          actions={
            <Button icon aria-label="Close audit panel" onClick={() => setAuditOpen(false)}>
              <Ic.Close size={14} />
            </Button>
          }
          style={{ marginBottom: 12 }}
        >
          {auditRows.length === 0 ? (
            <EmptyState icon={<Ic.ShieldCheck size={22} />}>No verified facts to audit yet.</EmptyState>
          ) : (
            <Table columns={["Status", "Claim", "Verified", ""]}>
              {auditRows.map(({ fact: f, age }) => {
                const stale = age != null && age > STALE_AFTER_DAYS;
                const taskState = auditTasks[f.claim];
                return (
                  <tr key={f.claim}>
                    <td>
                      <Chip tone={stale ? "gold" : "green"} dot>
                        {stale ? "Stale candidate" : "Fresh"}
                      </Chip>
                    </td>
                    <td><b>{f.claim}</b></td>
                    <td>
                      <span className="card__hint">
                        {fmtDate(f.verified)}{age != null ? ` · ${age}d ago` : ""}
                      </span>
                    </td>
                    <td>
                      {stale && (
                        <Button compact disabled={taskState === "creating" || taskState === "done"} onClick={() => createReviewTask(f)}>
                          {taskState === "done"
                            ? <><Ic.Check size={12} strokeWidth={2.2} /> Task created</>
                            : taskState === "creating" ? "Creating…" : "Create review task"}
                        </Button>
                      )}
                      {taskState === "error" && <span className="card__hint">Couldn’t reach Mari.</span>}
                    </td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Card>
      )}

      {/* add-fact composer */}
      {composerOpen && (
        <Card style={{ marginBottom: 12 }}>
          <div className="row">
            <Input
              placeholder="Claim — e.g. Free tier ends September 1"
              value={newClaim}
              onChange={(e) => setNewClaim(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addFact()}
              autoFocus
            />
            <Input
              placeholder="Source — e.g. Pricing sheet"
              value={newSource}
              onChange={(e) => setNewSource(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addFact()}
            />
            <Button variant="primary" onClick={addFact} disabled={adding || !newClaim.trim() || !newSource.trim()}>
              {adding ? "Adding…" : "Add fact"}
            </Button>
            <Button onClick={() => setComposerOpen(false)}>Cancel</Button>
          </div>
        </Card>
      )}

      <Card variant="flush">
        <Table columns={["Claim", "Owner", "Status", "Verified", ""]}>
          {visible.length === 0 && (
            <tr>
              <td colSpan={5}>
                <EmptyState icon={<Ic.Quill size={22} />}>
                  No {filter === "All" ? "" : `${filter.toLowerCase()} `}facts yet — capture one or scan recent documents.
                </EmptyState>
              </td>
            </tr>
          )}
          {visible.map((f) => {
            const im = impact[f.claim];
            const fresh = f.source.startsWith("Mari scan");
            return (
              <Fragment key={f.claim}>
                <tr className="factrow">
                  <td>
                    <span className="fact-claim">
                      {fresh && (
                        <span title="Found by Mari scan" aria-label="Found by Mari scan">
                          <Ic.Sparkle size={13} />{" "}
                        </span>
                      )}
                      {f.claim}
                    </span>
                    <span className="fact-src">Source: {f.source}</span>
                  </td>
                  <td>
                    <span className="row">
                      <Avatar name={f.owner} tint={(f.ownerTint as 1 | 2 | 3 | 4) || undefined} size="sm" />
                      {f.owner}
                    </span>
                  </td>
                  <td><StatusChip status={STATUS_CHIP[f.status] ?? "needs-review"} /></td>
                  <td><span className="card__hint">{fmtDate(f.verified)}</span></td>
                  <td>
                    <span className="row">
                      <Button compact onClick={() => runImpact(f)}>
                        {im?.loading ? "Analyzing…" : im?.docs && im.open ? "Hide impact" : "Impact"}
                      </Button>
                      {f.status !== "Verified" && (
                        <Button compact onClick={() => verify(f)}>Verify</Button>
                      )}
                    </span>
                  </td>
                </tr>
                {im?.open && (
                  <tr>
                    <td colSpan={5}>
                      <ImpactPanel
                        boxed
                        loading={im.loading}
                        summary={im.summary}
                        docs={im.docs}
                        onClose={() => setImpact((m) => ({ ...m, [f.claim]: { ...im, open: false } }))}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </Table>
      </Card>
    </>
  );
}

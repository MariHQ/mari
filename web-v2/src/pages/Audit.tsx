// Repository audit — onboarding / re-audit workflow for connected repos.
// Scan for markdown docs, localization variants, git authorship, tag coverage
// and repo hygiene, then walk a checklist of fixes until it clears.
// References: output/imagegen/mari-cloud-repo-audit.png
//             output/imagegen/mari-cloud-editorial-notebook.png

import { useMemo, useRef, useState } from "react";
import * as Ic from "../components/icons";
import { GitHubMark } from "../components/icons";
import { Button, Card, CountChip, EmptyState, PageHeader, Select, Spinner, Switch } from "../components/ui";
import { gql, useQuery } from "../lib/api";
import "./audit.css";

type Run = { id: number; provider: string; repo: string; findings: number; fixed: number; ranAt: string };
type Finding = {
  id: number; runId: number; kind: string; title: string; detail: string;
  fixAction: string; fixPayload: Record<string, any> | null; status: string;
};
type Member = { id: number; name: string };
type Override = { status: string; summary?: string };

const KINDS = ["localization", "tags", "authorship", "coverage", "hygiene"] as const;

const KIND_META: Record<string, { label: string; chip: string; icon: JSX.Element }> = {
  localization: { label: "Localization", chip: "localization gaps", icon: <Ic.Globe size={16} /> },
  tags: { label: "Tags", chip: "untagged", icon: <Ic.Tag size={16} /> },
  authorship: { label: "Authorship", chip: "unmapped authors", icon: <PersonIcon size={16} /> },
  coverage: { label: "Coverage", chip: "coverage", icon: <Ic.Doc size={16} /> },
  hygiene: { label: "Hygiene", chip: "hygiene", icon: <Ic.ShieldCheck size={16} /> },
};

// hand-drawn person mark (matches the 1.6px line-art icon set)
function PersonIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M5 20 c0.5 -4.5 3.4 -6.5 7 -6.5 s6.5 2 7 6.5" />
    </svg>
  );
}

const FIELDS = "id runId kind title detail fixAction fixPayload status";

const FIX_LABEL: Record<string, string> = {
  translation_task: "Create translation task",
  link_translation: "Link as translation",
  ingest: "Index it",
  hygiene_task: "Create task",
};

export default function AuditPage() {
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);
  const [busyIds, setBusyIds] = useState<number[]>([]);
  const [busyKind, setBusyKind] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<number, Override>>({});
  const [memberPick, setMemberPick] = useState<Record<number, string>>({});
  const [hideHandled, setHideHandled] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [pulseKind, setPulseKind] = useState<string | null>(null);
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const runsQ = useQuery<Run[]>(`{ auditRuns { id provider repo findings fixed ranAt } }`, {
    map: (d: any) => d.auditRuns ?? [],
  });
  const membersQ = useQuery<Member[]>(`{ members { id name } }`, { map: (d: any) => d.members ?? [] });
  const runs = useMemo(() => runsQ.data ?? [], [runsQ.data]);
  const members = membersQ.data ?? [];

  const latestRun = useMemo(
    () => runs.reduce<Run | null>((best, r) => (best == null || r.id > best.id ? r : best), null),
    [runs]
  );
  const activeRun = selectedRunId == null ? latestRun : runs.find((r) => r.id === selectedRunId) ?? latestRun;

  const findingsQuery = selectedRunId == null
    ? `{ auditFindings { ${FIELDS} } }`
    : `{ auditFindings(runId: ${selectedRunId}) { ${FIELDS} } }`;
  const findingsQ = useQuery<Finding[]>(findingsQuery, { map: (d: any) => d.auditFindings ?? [] });
  const findings = useMemo(() => findingsQ.data ?? [], [findingsQ.data]);

  const refetch = () => {
    runsQ.refetch();
    findingsQ.refetch();
  };

  const statusOf = (f: Finding) => overrides[f.id]?.status ?? f.status;

  const byKind = useMemo(() => {
    const map: Record<string, Finding[]> = {};
    for (const k of KINDS) map[k] = [];
    for (const f of findings) (map[f.kind] ?? (map[f.kind] = [])).push(f);
    for (const k of Object.keys(map)) map[k].sort((a, b) => Math.abs(a.id) - Math.abs(b.id));
    return map;
  }, [findings]);

  const total = findings.length;
  const handled = findings.filter((f) => statusOf(f) !== "open").length;

  /* ————————————————— actions ————————————————— */

  const reaudit = async () => {
    if (scanning) return;
    setScanning(true);
    await gql(`mutation { runRepoAudit }`);
    setSelectedRunId(null);
    setOverrides({});
    refetch();
    window.setTimeout(() => setScanning(false), 500);
  };

  const fixFinding = async (f: Finding, memberName?: string) => {
    if (busyIds.includes(f.id)) return;
    setBusyIds((b) => [...b, f.id]);
    const data = await gql<{ fixAuditFinding: string }>(
      `mutation($id: Int!, $m: String) { fixAuditFinding(id: $id, memberName: $m) }`,
      { id: f.id, m: memberName ?? null }
    );
    const summary = data?.fixAuditFinding ?? "fixed locally (API offline)";
    setOverrides((o) => ({ ...o, [f.id]: { status: "fixed", summary } }));
    setBusyIds((b) => b.filter((id) => id !== f.id));
    if (data) refetch();
  };

  const fixAll = async (kind: string) => {
    if (busyKind || !activeRun) return;
    const open = byKind[kind]?.filter((f) => statusOf(f) === "open") ?? [];
    if (open.length === 0) return;
    setBusyKind(kind);
    const data = await gql<{ fixAllAuditFindings: number }>(
      `mutation($r: Int!, $k: String!) { fixAllAuditFindings(runId: $r, kind: $k) }`,
      { r: activeRun.id, k: kind }
    );
    if (data) {
      refetch();
    } else {
      // API offline — flip the open rows locally so the checklist still clears
      setOverrides((o) => {
        const next = { ...o };
        for (const f of open) next[f.id] = { status: "fixed", summary: "fixed locally (API offline)" };
        return next;
      });
    }
    setBusyKind(null);
  };

  const dismiss = async (f: Finding) => {
    if (busyIds.includes(f.id)) return;
    setBusyIds((b) => [...b, f.id]);
    const ok = await gql(`mutation($id: Int!) { dismissAuditFinding(id: $id) }`, { id: f.id });
    setOverrides((o) => ({ ...o, [f.id]: { status: "dismissed" } }));
    setBusyIds((b) => b.filter((id) => id !== f.id));
    if (ok) refetch();
  };

  const loadRun = (id: number) => {
    setSelectedRunId(id === latestRun?.id ? null : id);
    setOverrides({});
  };

  const jumpTo = (kind: string) => {
    setCollapsed((c) => ({ ...c, [kind]: false }));
    sectionRefs.current[kind]?.scrollIntoView({ behavior: "smooth", block: "start" });
    setPulseKind(kind);
    window.setTimeout(() => setPulseKind((p) => (p === kind ? null : p)), 1400);
  };

  /* ————————————————— row bits ————————————————— */

  const marker = (status: string) => (
    <span className={`audit-mark audit-mark--${status}`} aria-hidden>
      {status === "fixed" && <Ic.Check size={13} strokeWidth={2.2} />}
      {status === "dismissed" && <span className="audit-mark__x">✕</span>}
    </span>
  );

  const fixControl = (f: Finding) => {
    const busy = busyIds.includes(f.id);
    if (f.fixAction === "apply_tag") {
      const tag = f.fixPayload?.tag ?? f.fixPayload?.suggest ?? "tag";
      return (
        <Button compact disabled={busy} onClick={() => fixFinding(f)}>
          {busy ? "Applying…" : <><Ic.Tag size={13} /> Apply ‘{tag}’</>}
        </Button>
      );
    }
    if (f.fixAction === "invite_member") {
      const pick = memberPick[f.id] ?? "";
      return (
        <span className="audit-invite">
          <Select
            className="audit-select"
            value={pick}
            aria-label="Map to member"
            onChange={(e) => setMemberPick((m) => ({ ...m, [f.id]: e.target.value }))}
          >
            <option value="">Map to member…</option>
            {members.map((m) => <option key={m.id} value={m.name}>{m.name}</option>)}
          </Select>
          <Button compact disabled={busy || !pick} onClick={() => fixFinding(f, pick)}>
            {busy ? "Mapping…" : "Map"}
          </Button>
          <Button variant="link" className="audit-invite__alt" disabled={busy} onClick={() => fixFinding(f)}>
            Invite as new member
          </Button>
        </span>
      );
    }
    const label = FIX_LABEL[f.fixAction] ?? "Fix";
    return (
      <Button compact disabled={busy} onClick={() => fixFinding(f)}>
        {busy ? "Working…" : label}
      </Button>
    );
  };

  /* ————————————————— page ————————————————— */

  const noRuns = runs.length === 0;

  return (
    <>
      <PageHeader
        title={`Repository audit${activeRun ? ` — ${activeRun.repo}` : ""}`}
        actions={
          <Button variant="primary" disabled={scanning} onClick={reaudit}>
            <Ic.Refresh size={14} /> {scanning ? "Scanning…" : noRuns ? "Run first audit" : "Re-audit"}
          </Button>
        }
      />
      <div className="audit-sub">
        {scanning ? (
          <span className="audit-scanning">
            <span className="audit-spin"><Ic.Refresh size={13} /></span>
            Scanning repo — markdown, localizations, authorship…
          </span>
        ) : activeRun ? (
          <span className="audit-sub__row">
            <GitHubMark size={15} /> {activeRun.provider === "github" ? "GitHub" : activeRun.provider} / {activeRun.repo}
            <span className="audit-sub__dot">·</span>
            Last audit: {activeRun.ranAt}
            <span className="audit-sub__dot">·</span>
            {activeRun.findings} findings, {activeRun.fixed} fixed
          </span>
        ) : (
          "Connect a repo to begin."
        )}
      </div>

      {runsQ.loading ? (
        <Card variant="plain">
          <div style={{ display: "grid", placeItems: "center", minHeight: 160 }}>
            <Spinner size="sm" label="Loading audits" />
          </div>
        </Card>
      ) : runsQ.error && !runsQ.data ? (
        <Card variant="plain">
          <EmptyState>API offline — audit history is unavailable.</EmptyState>
        </Card>
      ) : noRuns ? (
        <Card variant="plain">
          <EmptyState
            icon={<Ic.Clipboard size={26} />}
            title="No audits yet"
            action={
              <Button variant="primary" disabled={scanning} onClick={reaudit}>
                <Ic.Refresh size={14} /> {scanning ? "Scanning…" : "Run first audit"}
              </Button>
            }
          >
            Connect a repo and run your first audit — Mari scans markdown docs, localization variants, git authorship,
            tags and repo hygiene, then walks you through the fixes.
          </EmptyState>
        </Card>
      ) : (
        <>
          {/* summary strip — chips filter/scroll to their section */}
          <Card className="audit-strip">
            {KINDS.map((k) => {
              const open = byKind[k].filter((f) => statusOf(f) === "open").length;
              return (
                <button key={k} className={`audit-chip${open === 0 ? " is-clear" : ""}`} onClick={() => jumpTo(k)}>
                  <span className={`audit-chip__icon audit-chip__icon--${k}`}>{KIND_META[k].icon}</span>
                  <b>{open}</b>
                  <span>{KIND_META[k].chip}</span>
                </button>
              );
            })}
          </Card>

          <div className="audit-cols">
            <div className="audit-main">
              {/* progress header */}
              <Card className="audit-progress">
                <span className="audit-progress__label"><b>{handled}</b> of <b>{total}</b> handled</span>
                <span className="audit-bar">
                  <span className="audit-bar__fill" style={{ width: total ? `${(handled / total) * 100}%` : "0%" }} />
                </span>
                <Switch checked={hideHandled} onCheckedChange={setHideHandled} label="Hide handled" />
              </Card>

              {/* findings, grouped by kind */}
              {findingsQ.loading && (
                <Card variant="flush" className="audit-sec">
                  <div style={{ display: "grid", placeItems: "center", minHeight: 120 }}>
                    <Spinner size="sm" label="Loading findings" />
                  </div>
                </Card>
              )}
              {findingsQ.error && !findingsQ.data && (
                <Card variant="flush" className="audit-sec">
                  <EmptyState>API offline — findings unavailable.</EmptyState>
                </Card>
              )}
              {KINDS.map((kind) => {
                const rows = byKind[kind];
                if (rows.length === 0) return null;
                const openCount = rows.filter((f) => statusOf(f) === "open").length;
                const isCollapsed = collapsed[kind] ?? false;
                const visible = hideHandled ? rows.filter((f) => statusOf(f) === "open") : rows;
                return (
                  <div key={kind} ref={(el) => { sectionRefs.current[kind] = el; }}>
                    <Card variant="flush" className={`audit-sec${pulseKind === kind ? " is-pulsed" : ""}`}>
                      <div className="audit-sec__head">
                        <button
                          className="audit-sec__toggle"
                          aria-expanded={!isCollapsed}
                          onClick={() => setCollapsed((c) => ({ ...c, [kind]: !isCollapsed }))}
                        >
                          <span className={`audit-sec__chev${isCollapsed ? " is-collapsed" : ""}`}><Ic.Chev size={14} /></span>
                          <span className={`audit-sec__icon audit-chip__icon--${kind}`}>{KIND_META[kind].icon}</span>
                          <span className="audit-sec__title">{KIND_META[kind].label}</span>
                          <CountChip value={openCount} />
                        </button>
                        {openCount > 0 && (
                          <Button variant="link" className="audit-fixall" disabled={busyKind === kind} onClick={() => fixAll(kind)}>
                            {busyKind === kind ? "Fixing…" : "Fix all"}
                          </Button>
                        )}
                      </div>
                      {!isCollapsed && (
                        <div className="audit-rows">
                          {visible.length === 0 && (
                            <div className="audit-row audit-row--none card__hint">All handled — nothing hidden here but tidy checkmarks.</div>
                          )}
                          {visible.map((f) => {
                            const status = statusOf(f);
                            const summary = overrides[f.id]?.summary;
                            return (
                              <div key={f.id} className={`audit-row audit-row--${status}`}>
                                {marker(status)}
                                <span className="audit-row__body">
                                  <span className="audit-row__title">{f.title}</span>
                                  <span className="audit-row__detail">{f.detail}</span>
                                  {status === "fixed" && summary && (
                                    <span className="audit-row__done">→ {summary}</span>
                                  )}
                                  {status === "dismissed" && <span className="audit-row__done audit-row__done--dim">dismissed</span>}
                                </span>
                                {status === "open" && (
                                  <span className="audit-row__actions">
                                    {fixControl(f)}
                                    <Button
                                      icon
                                      compact
                                      className="audit-dismiss"
                                      aria-label={`Dismiss: ${f.title}`}
                                      title="Dismiss"
                                      disabled={busyIds.includes(f.id)}
                                      onClick={() => dismiss(f)}
                                    >
                                      <Ic.Close size={13} />
                                    </Button>
                                  </span>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </Card>
                  </div>
                );
              })}
            </div>

            {/* right rail */}
            <aside className="audit-rail">
              <Card className="audit-railcard" title="Audit history">
                {runs.map((r) => {
                  const active = r.id === activeRun?.id;
                  const pct = r.findings ? Math.round((r.fixed / r.findings) * 100) : 100;
                  return (
                    <button key={r.id} className={`audit-run${active ? " is-active" : ""}`} onClick={() => loadRun(r.id)}>
                      <span className={`audit-run__dot${r.fixed >= r.findings ? " is-done" : ""}`}>
                        {r.fixed >= r.findings ? <Ic.Check size={11} strokeWidth={2.4} /> : "!"}
                      </span>
                      <span className="audit-run__body">
                        <span className="audit-run__when">{r.ranAt}</span>
                        <span className="audit-run__meta">{r.findings} findings · {r.fixed} fixed</span>
                        <span className="audit-run__bar"><span style={{ width: `${pct}%` }} /></span>
                      </span>
                    </button>
                  );
                })}
              </Card>

              <Card
                className="audit-railcard"
                title="What we scan"
                actions={<span className="audit-railcard__mark"><Ic.Clipboard size={17} /></span>}
              >
                <ul className="audit-scanlist">
                  <li><Ic.Doc size={13} /> Markdown docs &amp; index coverage</li>
                  <li><Ic.Globe size={13} /> Localization variants (*.es.md, *.fr.md)</li>
                  <li><PersonIcon size={13} /> Git authorship ↔ workspace members</li>
                  <li><Ic.Tag size={13} /> Tag coverage &amp; suggestions</li>
                  <li><Ic.ShieldCheck size={13} /> Repo hygiene (LICENSE, README…)</li>
                </ul>
              </Card>
            </aside>
          </div>
        </>
      )}
    </>
  );
}

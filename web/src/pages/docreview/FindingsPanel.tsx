// Bottom-right card: fact check — claim tallies, the contradiction detail, the
// all-claims list, and the create-review-task row.

import { Fragment, useState } from "react";
import * as Ic from "../../components/icons";
import {
  Avatar, Button, Card, Menu, MenuRadioGroup, MenuRadioItem, StatusChip, Tabs,
} from "../../components/ui";
import { gql } from "../../lib/api";
import { TASK_DUES, TASK_OWNERS, TASK_PRIS, type Finding } from "./data";

export type FactTab = "check" | "claims";

type Claim = { claim: string; source: string; status: string; verified: string };

type FindingsPanelProps = {
  tab: FactTab;
  onTab: (tab: FactTab) => void;
  supportedN: number;
  contradictionN: number;
  unsupportedN: number;
  allClaimsN: number;
  checking: boolean;
  onRunCheck: () => void;
  claims: Claim[];
  contra: Finding | null;
  evidence: string;
  onJumpToFinding: (fid: number) => void;
  onOpenSource: () => void;
};

export function FindingsPanel({
  tab, onTab, supportedN, contradictionN, unsupportedN, allClaimsN,
  checking, onRunCheck, claims, contra, evidence, onJumpToFinding, onOpenSource,
}: FindingsPanelProps) {
  const [taskState, setTaskState] = useState<"idle" | "creating" | "done">("idle");
  const [ownerIx, setOwnerIx] = useState(0);
  const [dueIx, setDueIx] = useState(0);
  const [priIx, setPriIx] = useState(0);

  const createTask = async () => {
    if (taskState !== "idle") return;
    setTaskState("creating");
    await gql(
      `mutation($title: String!, $assignee: String) { createTask(title: $title, kind: "factcheck", kindLabel: "Fact check", assignee: $assignee) }`,
      { title: `Review: ${contra?.text ?? "fact check findings"}`, assignee: TASK_OWNERS[ownerIx].name },
    );
    setTaskState("done");
  };

  const tallies: [string, string, "green" | "red" | "gold", React.ReactNode][] = [
    [String(supportedN), "Supported", "green", <Ic.Check size={14} key="s" />],
    [String(contradictionN), "Contradiction", "red", "!"],
    [String(unsupportedN), "Unsupported", "gold", "!"],
  ];

  return (
    <Card variant="flush" className="dr-fc">
      <div className="dr-queue__tabs">
        <Tabs<FactTab>
          value={tab}
          onChange={onTab}
          ariaLabel="Fact check"
          variant="underline"
          options={[
            { id: "check", label: "Fact check" },
            { id: "claims", label: "All claims", count: allClaimsN },
          ]}
        />
      </div>

      <div className="row dr-tallies dr-fc__tallies">
        {tallies.map(([n, label, tone, icon], i) => (
          <Fragment key={label}>
            {i > 0 && <span className="dr-tallies__rule" />}
            <span className={`dr-fc__tally dr-fc__tally--${tone}`}>
              <span className="row dr-fc__tally-head">{icon} {label}</span>
              <b>{n}</b>
            </span>
          </Fragment>
        ))}
      </div>

      <div className="dr-fc__run">
        <Button block disabled={checking} onClick={onRunCheck}>
          <Ic.ShieldCheck size={14} /> {checking ? "Checking claims against verified facts…" : "Run fact check"}
        </Button>
      </div>

      {tab === "claims" ? (
        <div className="dr-claims">
          {claims.map((f) => (
            <div key={f.claim} className="row dr-claim">
              <span className={`dr-claim__ic${f.status === "Verified" ? " dr-claim__ic--ok" : ""}`}><Ic.CheckCircle size={14} /></span>
              <span className="dr-claim__body">
                {f.claim}
                <span className="card__hint dr-claim__meta">{f.source} · {f.status}{f.verified !== "—" ? ` · ${f.verified}` : ""}</span>
              </span>
            </div>
          ))}
        </div>
      ) : (
        <>
          {contra ? (
            <div className="dr-contra">
              <div className="row dr-contra__head">
                <span className="row dr-contra__label">
                  <span className="dr-contra__bang">!</span> Contradiction
                </span>
                <span className="card__hint">High confidence</span>
              </div>
              <div className="dr-contra__sec">
                <span className="card__hint dr-contra__cap">In document</span>
                <mark className="dr-contra__mark dr-contra__mark--doc" onClick={() => onJumpToFinding(contra.id)}>{contra.text}</mark>
              </div>
              <div className="dr-contra__sec dr-contra__sec--evidence">
                <span className="card__hint dr-contra__cap">Verified evidence</span>
                <mark className="dr-contra__mark dr-contra__mark--evidence">{evidence}</mark>
              </div>
              <div className="row dr-contra__src">
                <Ic.Doc size={13} /> Auth RFC v1.2 · May 1, 2024
                <StatusChip status="canonical" />
                <Button variant="link" className="dr-contra__open" onClick={onOpenSource}>
                  Open source <Ic.ArrowR size={11} />
                </Button>
              </div>
            </div>
          ) : (
            <div className="card__hint dr-fc__clean">
              No contradictions found. Run a fact check to scan the latest claims.
            </div>
          )}

          <div className="dr-task">
            <div>
              <span className="card__hint">Owner</span>
              <Menu
                align="start"
                trigger={
                  <Button className="dr-task__pick">
                    <span className="row dr-task__val">
                      <Avatar size="sm" name={TASK_OWNERS[ownerIx].name} initials={TASK_OWNERS[ownerIx].init} tint={TASK_OWNERS[ownerIx].tint as 1 | 2} />
                      {TASK_OWNERS[ownerIx].name}
                    </span>
                    <Ic.Chev size={12} />
                  </Button>
                }
              >
                <MenuRadioGroup value={String(ownerIx)} onValueChange={(v) => setOwnerIx(Number(v))}>
                  {TASK_OWNERS.map((o, i) => <MenuRadioItem key={o.name} value={String(i)}>{o.name}</MenuRadioItem>)}
                </MenuRadioGroup>
              </Menu>
            </div>
            <div>
              <span className="card__hint">Due date</span>
              <Menu
                align="start"
                trigger={
                  <Button className="dr-task__pick">
                    <span className="row dr-task__val"><Ic.Calendar size={13} /> {TASK_DUES[dueIx]}</span>
                    <Ic.Chev size={12} />
                  </Button>
                }
              >
                <MenuRadioGroup value={String(dueIx)} onValueChange={(v) => setDueIx(Number(v))}>
                  {TASK_DUES.map((d, i) => <MenuRadioItem key={d} value={String(i)}>{d}</MenuRadioItem>)}
                </MenuRadioGroup>
              </Menu>
            </div>
            <div>
              <span className="card__hint">Priority</span>
              <Menu
                align="start"
                trigger={
                  <Button className="dr-task__pick">
                    <span className="row dr-task__val">
                      <span className="dr-task__pri" style={{ background: TASK_PRIS[priIx][1] }} /> {TASK_PRIS[priIx][0]}
                    </span>
                    <Ic.Chev size={12} />
                  </Button>
                }
              >
                <MenuRadioGroup value={String(priIx)} onValueChange={(v) => setPriIx(Number(v))}>
                  {TASK_PRIS.map(([label], i) => <MenuRadioItem key={label} value={String(i)}>{label}</MenuRadioItem>)}
                </MenuRadioGroup>
              </Menu>
            </div>
            <div className="dr-task__create">
              <Button variant="primary" block onClick={() => void createTask()} disabled={taskState !== "idle"}>
                {taskState === "done" ? "Task created ✓" : taskState === "creating" ? "Creating…" : "Create review task"}
              </Button>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

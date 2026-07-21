// Right rail: the Refine panel — skill grid, scope, findings tallies, run.

import { Fragment } from "react";
import * as Ic from "../../components/icons";
import { Button, Card, Menu, MenuRadioGroup, MenuRadioItem } from "../../components/ui";
import { SKILLS } from "./data";

export type RefineScope = "Whole document" | "Current selection";

type RefinePanelProps = {
  skill: string;
  onPickSkill: (name: string) => void;
  scope: RefineScope;
  onPickScope: (scope: RefineScope) => void;
  refining: boolean;
  needsSelection: boolean;
  lastRun: string | null;
  onRun: () => void;
  errorN: number;
  warnN: number;
  advisoryN: number;
};

export function RefinePanel({
  skill, onPickSkill, scope, onPickScope, refining, needsSelection, lastRun, onRun,
  errorN, warnN, advisoryN,
}: RefinePanelProps) {
  return (
    <Card title="Refine" className="dr-refine">
      <div className="dr-skills">
        {SKILLS.map((s) => (
          <Button key={s.name} className="dr-skill" aria-pressed={skill === s.name} onClick={() => onPickSkill(s.name)}>
            <b>{s.name}</b>
            <span>{s.sub}</span>
          </Button>
        ))}
      </div>

      <div className="row dr-refine__scope">
        <span className="dr-label">Scope</span>
        <Menu
          trigger={<Button compact>{scope} <Ic.Chev size={11} /></Button>}
        >
          <MenuRadioGroup value={scope} onValueChange={(v) => onPickScope(v as RefineScope)}>
            <MenuRadioItem value="Whole document">Whole document</MenuRadioItem>
            <MenuRadioItem value="Current selection">Current selection</MenuRadioItem>
          </MenuRadioGroup>
        </Menu>
      </div>

      <div className="dr-refine__findings">
        <span className="dr-label">Findings</span>
        <div className="row dr-tallies">
          {[[String(errorN), "Error", "var(--red)"], [String(warnN), "Warning", "var(--gold)"], [String(advisoryN), "Advisory", "var(--blue)"]].map(([n, l, c], i) => (
            <Fragment key={l}>
              {i > 0 && <span className="dr-tallies__rule" />}
              <span className="dr-tally">
                <b style={{ color: c }}>{n}</b>
                <span>{l}</span>
              </span>
            </Fragment>
          ))}
        </div>
      </div>

      <Button variant="primary" block className="dr-refine__run" onClick={onRun} disabled={refining || needsSelection}>
        <Ic.Sparkle size={15} /> {refining ? "Mari is editing…" : "Run refinement"}
      </Button>
      <div className="card__hint" style={{ marginTop: 8 }}>
        {refining
          ? `Running ${skill.toLowerCase()} on ${scope.toLowerCase()} — this can take up to a minute…`
          : needsSelection
            ? "Select text in the document to refine a selection — or switch scope to the whole document."
            : lastRun ?? "Proposals land in the review queue below — nothing changes until you accept."}
      </div>
    </Card>
  );
}

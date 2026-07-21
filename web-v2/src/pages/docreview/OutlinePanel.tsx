// Left rail: live document outline + revision history.

import * as Ic from "../../components/icons";
import { Button, Card, StatusChip } from "../../components/ui";
import { initialsOf, tintOf, type Rev } from "./data";

export type OutlineItem = { id: number; n: string; t: string; sub: boolean };

export function OutlinePanel({ items, onJump }: { items: OutlineItem[]; onJump: (id: number) => void }) {
  return (
    <Card title="Document outline">
      <div className="dr-outline-list">
        {items.map((o) => (
          <button key={o.id} className={`dr-outline${o.sub ? " dr-outline--sub" : ""}`} onClick={() => onJump(o.id)}>
            {!o.sub && <span className="dr-outline__dot" />}
            {o.n && <span className="dr-outline__n">{o.n}</span>}
            <span className="dr-outline__t">{o.t}</span>
          </button>
        ))}
        {items.length === 0 && <div className="card__hint">No headings yet — add a section with the H1 menu.</div>}
      </div>
    </Card>
  );
}

export function RevisionsPanel({
  revs,
  showAll,
  onToggleShowAll,
}: {
  revs: Rev[];
  showAll: boolean;
  onToggleShowAll: () => void;
}) {
  const visible = showAll ? revs : revs.slice(0, 4);
  return (
    <Card title="Revision history">
      <div className="dr-revs">
        {visible.map((r, i) => (
          <div key={r.id} className="row dr-rev">
            <span className={`avatar avatar--sm avatar--tint${tintOf(r.actor)}`}>{initialsOf(r.actor)}</span>
            <span className="dr-rev__body">
              {r.actor} {i === 0 && <StatusChip status="running" label="Current" className="dr-rev__current" />}
              <span className="card__hint" style={{ display: "block" }}>{r.verb} · {r.at}</span>
            </span>
          </div>
        ))}
      </div>
      {revs.length > 4 && (
        <Button variant="link" onClick={onToggleShowAll}>
          {showAll ? "Show fewer revisions" : `View all ${revs.length} revisions`} <Ic.ArrowR size={12} />
        </Button>
      )}
    </Card>
  );
}

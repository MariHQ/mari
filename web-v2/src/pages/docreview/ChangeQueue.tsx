// Bottom-left card: the review-before-apply queue. Proposed edits render as a
// word-level diff; accepting rewrites the body (handlers live in index.tsx).

import { Button, Card, Chip, EmptyState, Tabs } from "../../components/ui";
import { cleanText, diffChange } from "./markdown";
import type { Change } from "./data";

export type ChangeTab = "review" | "all";

type ChangeQueueProps = {
  tab: ChangeTab;
  onTab: (tab: ChangeTab) => void;
  changes: Change[];
  visible: Change[];
  pendingCount: number;
  /** Pending change whose original text is no longer in the body. */
  isApplied: (c: Change) => boolean;
  onAccept: (c: Change) => void;
  onReject: (c: Change) => void;
  onAcceptAll: () => void;
};

export function ChangeQueue({
  tab, onTab, changes, visible, pendingCount, isApplied, onAccept, onReject, onAcceptAll,
}: ChangeQueueProps) {
  return (
    <Card variant="flush" className="dr-queue">
      <div className="dr-queue__tabs">
        <Tabs<ChangeTab>
          value={tab}
          onChange={onTab}
          ariaLabel="Proposed changes"
          variant="underline"
          options={[
            { id: "review", label: "Review before apply" },
            { id: "all", label: "All changes", count: changes.length },
          ]}
        />
      </div>
      <div className="row dr-queue__cols">
        <span className="dr-queue__col dr-queue__col--orig">Original</span>
        <span className="dr-queue__col dr-queue__col--prop">⇄ Proposed</span>
        <span className="dr-queue__col-acts" />
      </div>
      {visible.map((c) => {
        const d = diffChange(cleanText(c.original), cleanText(c.proposed));
        const applied = isApplied(c);
        return (
          <div key={c.id} className={`row dr-change${c.state === "rejected" ? " dr-change--rejected" : ""}`}>
            <div className="dr-change__orig">
              {d.pre && <>{d.pre} </>}
              {d.delA && <s>{d.delA}</s>}
              {d.suf && <> {d.suf}</>}
            </div>
            <div className={`dr-change__prop${c.state === "accepted" ? " dr-change__prop--accepted" : ""}`}>
              {d.pre && <>{d.pre} </>}
              {d.delB && <b>{d.delB}</b>}
              {d.suf && <> {d.suf}</>}
            </div>
            <div className="dr-change__acts">
              <span className="dr-change__rule">Motivated by:<br /><b>{c.rule}</b></span>
              {c.state === "pending" ? (
                applied ? (
                  <>
                    <span className="card__hint dr-change__applied">already applied</span>
                    <Button compact onClick={() => onReject(c)}>Dismiss</Button>
                  </>
                ) : (
                  <>
                    <Button compact variant="success" onClick={() => onAccept(c)}>Accept</Button>
                    <Button compact variant="danger" onClick={() => onReject(c)}>Reject</Button>
                  </>
                )
              ) : (
                <Chip tone={c.state === "accepted" ? "green" : "terra"} dot>
                  {c.state === "accepted" ? "Accepted" : "Rejected"}
                </Chip>
              )}
            </div>
          </div>
        );
      })}
      {visible.length === 0 && (
        <EmptyState>No pending changes — run a refinement or switch to “All changes”.</EmptyState>
      )}
      <div className="row dr-queue__foot">
        <span className="card__hint">Showing {visible.length} of {changes.length} changes</span>
        <Button onClick={onAcceptAll} disabled={pendingCount === 0}>
          Accept all {pendingCount || ""} changes
        </Button>
      </div>
    </Card>
  );
}

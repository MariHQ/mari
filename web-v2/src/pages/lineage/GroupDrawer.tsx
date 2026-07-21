// Roll-up group drawer — the macro-node counterpart to NodeDrawer: group
// summary (count, kind, repo, top members by degree) plus the expand/collapse
// affordance. Same .lg-drawer shell so the canvas stays interactive.

import { GitHubMark } from "../../components/icons";
import { Button, Chip, fmtDate } from "../../components/ui";
import { groupKindWord, groupParts, LNode } from "./model";

export function GroupDrawer({ groupId, members, visibleCount, degreeOf, isOpen, onExpand, onCollapse, onSelectMember, onClose }: {
  groupId: string;
  members: LNode[]; // all members, already ranked by degree desc then recency
  visibleCount: number; // members matching the current filters
  degreeOf: (id: string) => number;
  isOpen: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  onSelectMember: (id: string) => void;
  onClose: () => void;
}) {
  const { repo, kind } = groupParts(groupId);
  const kindWord = groupKindWord(kind, members.length);
  const top = members.slice(0, 8);

  return (
    <aside className="lg-drawer" role="dialog" aria-label={`Details for ${members.length} ${kindWord} in ${repo}`}>
      <div className="lg-drawer__head">
        <span className="lg-drawer__icon"><GitHubMark size={19} /></span>
        <div className="lg-grow">
          <b className="lg-drawer__title">{members.length} {kindWord}</b>
          <div className="lg-drawer__pills">
            <Chip>{repo}</Chip>
            <Chip>Rolled up</Chip>
            {visibleCount !== members.length && <Chip tone="terra">{visibleCount} match filters</Chip>}
          </div>
        </div>
        <button className="kebab lg-drawer__close" onClick={onClose} aria-label="Close details drawer">✕</button>
      </div>

      <div className="lg-drawer__body">
        <div>
          <span className="lg-label">Group</span>
          <div className="lg-kv"><span>Repository</span><b>{repo}</b></div>
          <div className="lg-kv"><span>Kind</span><b>{groupKindWord(kind, 2)}</b></div>
          <div className="lg-kv"><span>Members</span><b>{members.length}</b></div>
          <div className="lg-kv"><span>Matching current filters</span><b>{visibleCount}</b></div>
        </div>
        <div className="lg-group">
          <span className="lg-label">Top members (by connections)</span>
          {top.map((m) => (
            <button key={m.id} className="lg-conn" onClick={() => onSelectMember(m.id)} title={`Show details for ${m.title}`}>
              <span className="lg-conn__main">
                <b>{m.title}</b>
                <span className="lg-conn__sub">
                  {[m.owner || null, m.date ? fmtDate(m.date) : null, `${degreeOf(m.id)} link${degreeOf(m.id) === 1 ? "" : "s"}`].filter(Boolean).join(" · ")}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="lg-drawer__foot">
        <div className="lg-actions">
          {isOpen
            ? <Button compact onClick={onCollapse} title="Collapse back to one node">⊖ Collapse group</Button>
            : <Button compact onClick={onExpand} title="Expand the top members in place">⊕ Expand group</Button>}
        </div>
      </div>
    </aside>
  );
}

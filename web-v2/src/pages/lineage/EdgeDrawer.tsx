// Edge detail drawer — same .lg-drawer shell, edge flavor.

import { Link } from "react-router-dom";
import * as Ic from "../../components/icons";
import { GitHubMark } from "../../components/icons";
import { Chip, fmtDate } from "../../components/ui";
import { nodeIcon } from "./glyphs";
import { LEdge, LNode, REL } from "./model";

export function EdgeDrawer({ edge, from, to, onSelect, onClose }: {
  edge: LEdge;
  from: LNode;
  to: LNode;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const r = REL[edge.rel];
  return (
    <aside className="lg-drawer" role="dialog" aria-label={`Edge ${from.title} to ${to.title}`}>
      <div className="lg-drawer__head">
        <span className="lg-drawer__icon">
          <svg width="18" height="8" aria-hidden><line x1="0" y1="4" x2="18" y2="4" stroke={r.color} strokeWidth="2.4" strokeDasharray={edge.llm || edge.dashed ? "4 4" : r.dash} /></svg>
        </span>
        <div className="lg-grow">
          <b className="lg-drawer__title lg-drawer__title--sm">{from.title} <Ic.ArrowR size={13} /> {to.title}</b>
          <div className="lg-drawer__pills">
            <Chip className={`lg-rel--${edge.rel}`}>{r.label}</Chip>
            {edge.llm && <Chip>Derived by Mari</Chip>}
          </div>
        </div>
        <button className="kebab lg-drawer__close" onClick={onClose} aria-label="Close edge drawer">✕</button>
      </div>
      <div className="lg-drawer__body">
        <div>
          <span className="lg-label">Link</span>
          <div className="lg-kv"><span>Relation</span><b style={{ color: r.color }}>{r.label.toLowerCase()}</b></div>
          <div className="lg-kv">
            <span>Status</span>
            {edge.meta?.status === "confirmed"
              ? <Chip tone="green" dot>Confirmed</Chip>
              : <b>{edge.llm ? "Derived by Mari" : "Observed"}</b>}
          </div>
          {edge.meta?.evidence && (
            <div className="lg-kv"><span>Evidence</span><b className="row" style={{ gap: 5 }}><GitHubMark size={13} /> {edge.meta.evidence}</b></div>
          )}
          {edge.date && <div className="lg-kv"><span>Last seen</span><b>{fmtDate(edge.date)}</b></div>}
          {edge.meta?.note && <div className="lg-summary">{edge.meta.note}</div>}
        </div>
        <div>
          <span className="lg-label">Endpoints</span>
          {[{ n: from, tag: "from" }, { n: to, tag: "to" }].map(({ n, tag }) => (
            <button key={tag} className="lg-conn" onClick={() => onSelect(n.id)} title={`Show details for ${n.title}`}>
              {nodeIcon(n.icon, n.source, 15)}
              <span className="lg-conn__main">
                <b>{tag === "from" ? "→ " : "← "}{n.title}</b>
                <span className="lg-conn__sub">{n.meta}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="lg-drawer__foot">
        <Link className="linklike lg-openlink" to={from.docId != null ? `/knowledge/doc?id=${from.docId}` : "/knowledge"}>
          Open source document <Ic.External size={11} />
        </Link>
      </div>
    </aside>
  );
}

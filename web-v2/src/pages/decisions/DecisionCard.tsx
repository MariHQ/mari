import * as Ic from "../../components/icons";
import { SourceIcon } from "../../components/shared";
import { Avatar, Button, Chip, Input, Menu, MenuItem, Spinner, fmtDate } from "../../components/ui";
import { Decision, ImpactState, SEVERITY, STAMP_LABEL, sourceKind } from "./data";

export type DecisionCardProps = {
  d: Decision;
  im?: ImpactState;
  ratifying: number | null;
  justStamped: number | null;
  showSupersedeForm: boolean;
  supersedeText: string;
  superseding: boolean;
  onRatify: (id: number) => void;
  onOpenSupersede: (id: number) => void;
  onCancelSupersede: () => void;
  onSupersedeTextChange: (value: string) => void;
  onSupersede: (id: number) => void;
  onRunImpact: (d: Decision) => void;
  onSetImpactOpen: (id: number, open: boolean) => void;
  onCreateTasks: (d: Decision) => void;
};

export function DecisionCard({
  d,
  im,
  ratifying,
  justStamped,
  showSupersedeForm,
  supersedeText,
  superseding,
  onRatify,
  onOpenSupersede,
  onCancelSupersede,
  onSupersedeTextChange,
  onSupersede,
  onRunImpact,
  onSetImpactOpen,
  onCreateTasks,
}: DecisionCardProps) {
  const fresh = d.sourceLabel.startsWith("Mari scan");
  return (
    <div className="dec-item">
      <span
        className={`dec-node${d.status === "ratified" ? " dec-node--ratified" : d.status === "superseded" ? " dec-node--superseded" : ""}`}
        aria-hidden="true"
      >
        {d.status === "ratified" && <Ic.Check size={11} />}
      </span>
      <article className={`card dec-card${d.status === "superseded" ? " dec-card--superseded" : ""}`}>
        <div className="dec-card__top">
          <h3 className="dec-statement">
            {fresh && (
              <span className="dec-spark" title="Found by Mari scan" aria-label="Found by Mari scan">✨</span>
            )}
            <q>{d.statement}</q>
          </h3>
          <span className={`dec-stamp dec-stamp--${d.status}${justStamped === d.id ? " dec-stamp--in" : ""}`}>
            {STAMP_LABEL[d.status]}
          </span>
        </div>

        {d.context && <p className="dec-context">{d.context}</p>}

        {d.status === "superseded" && d.supersededByStatement && (
          <div className="dec-superline">
            <Ic.ArrowR size={14} />
            <span>Superseded by <b>{`“${d.supersededByStatement}”`}</b></span>
          </div>
        )}

        <div className="dec-meta">
          {d.sourceLabel && (
            <Chip icon={<SourceIcon source={sourceKind(d.sourceLabel)} size={15} />}>{d.sourceLabel}</Chip>
          )}
          {d.owners.length > 0 && (
            <span className="dec-owners">
              {d.owners.map((o) => (
                <Avatar key={o} name={o} size="sm" />
              ))}
            </span>
          )}
          {d.decidedOn && <span className="dec-date">Decided {fmtDate(d.decidedOn)}</span>}
          <span className="dec-meta__actions">
            {d.status === "proposed" && (
              <Button variant="success" compact onClick={() => onRatify(d.id)} disabled={ratifying !== null}>
                <Ic.Check size={13} /> {ratifying === d.id ? "Ratifying…" : "Ratify"}
              </Button>
            )}
            {d.status === "ratified" && (
              <Menu
                trigger={
                  <Button icon compact aria-label="Decision actions">
                    <Ic.Kebab size={16} />
                  </Button>
                }
              >
                <MenuItem icon={<Ic.Refresh size={14} />} onSelect={() => onOpenSupersede(d.id)}>
                  Supersede…
                </MenuItem>
              </Menu>
            )}
          </span>
        </div>

        {showSupersedeForm && (
          <div className="dec-supersede">
            <span className="dec-supersede__label">Supersede with a new decision</span>
            <div className="row">
              <Input
                placeholder="New statement — the old record stays in the ledger, struck through"
                value={supersedeText}
                onChange={(e) => onSupersedeTextChange(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onSupersede(d.id)}
                autoFocus
              />
              <Button variant="primary" compact onClick={() => onSupersede(d.id)} disabled={superseding || !supersedeText.trim()}>
                {superseding ? "Superseding…" : "Supersede"}
              </Button>
              <Button compact onClick={onCancelSupersede}>Cancel</Button>
            </div>
          </div>
        )}

        {/* impact strip — ratified decisions only */}
        {d.status === "ratified" && (
          <div className="dec-impact">
            {im?.loading ? (
              <span className="dec-impact__running">
                <Spinner size="sm" label="Analyzing impact" /> Mari is tracing the blast radius — reading every document this decision touches…
              </span>
            ) : im?.open && im.docs ? (
              <>
                <div className="dec-impact__head">
                  <b>Impact analysis</b>
                  <Button variant="link" className="dec-impact__collapse" onClick={() => onSetImpactOpen(d.id, false)}>
                    <Ic.Chev size={13} /> Collapse
                  </Button>
                </div>
                {im.summary && <p className="dec-impact__summary">{im.summary}</p>}
                {im.docs.length > 0 && (
                  <div className="dec-impact__docs">
                    {im.docs.map((doc, i) => {
                      const sev = SEVERITY[doc.severity] ?? SEVERITY.minor;
                      return (
                        <div key={i} className="dec-doc">
                          <Chip tone={sev.tone} caps className="dec-doc__sev">{sev.label}</Chip>
                          <b>{doc.title}</b>
                          <span className="dec-doc__src">{doc.source}</span>
                          <span className="dec-doc__reason">{doc.reason}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
                {im.docs.length > 0 && (
                  <div className="dec-impact__foot">
                    {im.tasksCreated ? (
                      <span className="dec-impact__done">
                        <Ic.CheckCircle size={14} /> {im.tasksCreated} task{im.tasksCreated === 1 ? "" : "s"} created
                      </span>
                    ) : (
                      <Button compact onClick={() => onCreateTasks(d)} disabled={!!im.creatingTasks}>
                        <Ic.Clipboard size={13} />
                        {im.creatingTasks ? "Creating tasks…" : `Create ${im.docs.length} task${im.docs.length === 1 ? "" : "s"}`}
                      </Button>
                    )}
                  </div>
                )}
              </>
            ) : im?.docs ? (
              <Button variant="link" onClick={() => onRunImpact(d)}>
                <Ic.ChevR size={13} /> Show impact analysis
              </Button>
            ) : d.impactCount > 0 ? (
              <span className="dec-impact__persisted">
                <Ic.Layers size={15} />
                <span>
                  <b>{d.impactCount} document{d.impactCount === 1 ? "" : "s"} affected</b>
                  {d.impactSummary ? ` · ${d.impactSummary}` : ""}
                </span>
              </span>
            ) : (
              <Button compact onClick={() => onRunImpact(d)}>
                <Ic.Sparkle size={13} /> Run impact analysis
              </Button>
            )}
          </div>
        )}
      </article>
    </div>
  );
}

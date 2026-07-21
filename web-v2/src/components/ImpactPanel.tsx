// ImpactPanel — the one impact-analysis rendering, shared by Facts and
// Decisions (it was duplicated inline on both pages). Severity chips are the
// canonical SeverityChip; the close affordance is a Button icon + Ic.Close.

import { ReactNode } from "react";
import clsx from "clsx";
import * as Ic from "./icons";
import { Button, ChipSeverity, SeverityChip } from "./ui";
import "./ImpactPanel.css";

export type ImpactDoc = { title: string; source: string; severity: string; reason: string };

const SEVERITY: Record<string, { level: ChipSeverity; label: string }> = {
  "update-required": { level: "high", label: "Update required" },
  review: { level: "med", label: "Review" },
  minor: { level: "low", label: "Minor" },
};

export function ImpactPanel({
  loading = false,
  loadingText = "Mari is reading the graph — checking documents that depend on this claim…",
  summary,
  docs = [],
  onClose,
  footer,
  boxed = false,
}: {
  loading?: boolean;
  loadingText?: string;
  summary?: string;
  docs?: ImpactDoc[];
  onClose?: () => void;
  /** Extra actions under the doc list (e.g. Decisions' “Create N tasks”). */
  footer?: ReactNode;
  /** Bordered inset box — for hosts without their own strip chrome (Facts). */
  boxed?: boolean;
}) {
  if (loading) {
    return (
      <span className="impact-running">
        <span className="dot" aria-hidden="true" /> {loadingText}
      </span>
    );
  }
  return (
    <div className={clsx("impact-panel", boxed && "impact-panel--boxed")}>
      <div className="impact-panel__head">
        <b>Impact analysis</b>
        {onClose && (
          <Button icon aria-label="Hide impact analysis" onClick={onClose}>
            <Ic.Close size={14} />
          </Button>
        )}
      </div>
      {summary && <p className="impact-panel__summary">{summary}</p>}
      {docs.length === 0 && !summary && <span className="card__hint">No impacted documents found.</span>}
      {docs.length > 0 && (
        <div className="impact-panel__docs">
          {docs.map((doc, i) => {
            const sev = SEVERITY[doc.severity] ?? SEVERITY.minor;
            return (
              <div key={i} className="impact-doc">
                <SeverityChip level={sev.level} label={sev.label} />
                <b>{doc.title}</b>
                <span className="impact-doc__src">{doc.source}</span>
                <span className="impact-doc__reason">{doc.reason}</span>
              </div>
            );
          })}
        </div>
      )}
      {footer && <div className="impact-panel__foot">{footer}</div>}
    </div>
  );
}

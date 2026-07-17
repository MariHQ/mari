// Knowledge inspector — the sticky right-rail Card showing the selected
// document, its tags, verified facts, related results, and revision history.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import * as Ic from "../../components/icons";
import { Pill, SourceIcon } from "../../components/shared";
import { TagPicker } from "../../components/TagPicker";
import { Button, Card, CountChip, SectionLabel, Tabs, fmtDate } from "../../components/ui";
import { SourceKey } from "../../data/sources";
import { Result } from "./search";

export type InspectorDoc = {
  id: number;
  title: string;
  owner: string;
  updated: string;
  sourceKey: SourceKey;
  source: string;
  kind: string;
  summary: string;
  tags: string[];
  watched: boolean;
};

export type FactRow = { text: string; sub: string };
export type RelatedRow = { source: SourceKey; text: string; sub: string };
export type TimelineRow = { date: string; what: string; sub: string };

export function Inspector({
  insp,
  sel,
  factRows,
  relatedRows,
  timeline,
  onTagsChange,
}: {
  insp: InspectorDoc;
  sel: Result | undefined;
  factRows: FactRow[];
  relatedRows: RelatedRow[];
  timeline: TimelineRow[];
  onTagsChange: (id: number, tags: string[]) => void;
}) {
  const navigate = useNavigate();
  const [insTab, setInsTab] = useState<"DOCUMENT" | "INSPECTOR">("DOCUMENT");

  return (
    <Card variant="flush" className="inspector">
      <div style={{ padding: "8px 20px 0" }}>
        <Tabs
          variant="underline"
          ariaLabel="Inspector view"
          value={insTab}
          onChange={setInsTab}
          options={[
            { id: "DOCUMENT", label: "Document" },
            { id: "INSPECTOR", label: "Inspector" },
          ]}
        />
      </div>
      <div className="inspector__body">
        <div className="inspector__title">
          <SourceIcon source={insp.sourceKey} size={26} />
          {insp.title}
        </div>
        {insp.sourceKey === "slack" ? (
          <div className="inspector-conversation"><Ic.Chat size={14} /><span>Thread-sized search chunk · {sel?.messageCount ?? 18} messages · no per-message tagging</span></div>
        ) : (
          <div className="row" style={{ gap: 8 }}>
            {insp.tags.map((t) => <Pill key={t} kind={t} />)}
            <TagPicker compact tags={insp.tags} onChange={(tags) => onTagsChange(insp.id, tags)} />
          </div>
        )}

        {insTab === "INSPECTOR" ? (
          <div style={{ marginTop: 14 }}>
            {[
              ["ID", String(insp.id)],
              ["Kind", insp.kind],
              ["Source", insp.source],
              ["Owner", insp.owner],
              ["Updated", fmtDate(insp.updated)],
              ["Tags", insp.tags.join(", ") || "—"],
              ["Watched", insp.watched ? "Yes" : "No"],
            ].map(([k, v]) => (
              <div key={k} className="row" style={{ justifyContent: "space-between", padding: "7px 0", borderBottom: "1px solid var(--line-soft)" }}>
                <SectionLabel>{k}</SectionLabel>
                <span style={{ font: "13.5px var(--serif)" }}>{v}</span>
              </div>
            ))}
            <Button block style={{ marginTop: 14 }} onClick={() => navigate(`/knowledge/doc?id=${insp.id}`)}>
              Open document <Ic.ArrowR size={14} />
            </Button>
          </div>
        ) : (
          <>
            <div className="row" style={{ gap: 0, marginTop: 14, borderTop: "1px solid var(--line-soft)", borderBottom: "1px solid var(--line-soft)" }}>
              {[
                { h: "Owner", v: insp.owner },
                { h: "Last updated", v: fmtDate(insp.updated) },
                { h: "Source", v: insp.source },
              ].map((c, i) => (
                <div key={c.h} style={{ flex: 1, padding: i ? "10px 0 10px 12px" : "10px 0", borderLeft: i ? "1px solid var(--line-soft)" : "none" }}>
                  <SectionLabel>{c.h}</SectionLabel>
                  <div style={{ font: "13.5px var(--serif)", marginTop: 3 }}>{c.v}</div>
                </div>
              ))}
            </div>

            <h5>Summary</h5>
            <p style={{ margin: 0, font: "14px/1.55 var(--serif)", color: "#46412f" }}>{insp.summary}</p>

            <h5>Verified facts <CountChip value={factRows.length} /></h5>
            <div className="inspector__facts">
              {factRows.map((f) => (
                <div className="ifact" key={f.text}>
                  <Ic.CheckCircle size={15} />
                  <span>{f.text}<span className="sub">{f.sub}</span></span>
                </div>
              ))}
            </div>

            <h5>Related <CountChip value={relatedRows.length} /></h5>
            <div className="inspector__facts">
              {relatedRows.map((f) => (
                <div className="ifact" key={f.text}>
                  <SourceIcon source={f.source} size={16} />
                  <span>{f.text}<span className="sub">{f.sub}</span></span>
                </div>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 16 }}>
              <div>
                <h5>Revision timeline</h5>
                <div className="timeline">
                  {timeline.map((t) => (
                    <div className="timeline__item" key={t.date + t.what}>
                      {/* revision `at` arrives pre-formatted ("May 11, 4:12 PM") — don't re-parse */}
                      <b style={{ fontWeight: 600 }}>{t.date}</b> — {t.what}
                      {t.sub && <span className="sub" style={{ display: "block" }}>{t.sub}</span>}
                    </div>
                  ))}
                </div>
                <Button block style={{ marginTop: 12 }} onClick={() => navigate(`/knowledge/doc?id=${insp.id}`)}>
                  View full history <Ic.ArrowR size={14} />
                </Button>
              </div>
              <div>
                <h5>Open in lineage</h5>
                <svg viewBox="0 0 150 96" width="100%" aria-hidden style={{ background: "var(--card-inner)", borderRadius: 8, border: "1px solid var(--line-soft)" }}>
                  <g stroke="#a99d81" strokeWidth="1.3" strokeDasharray="3 4" filter="url(#sketch-soft)">
                    <path d="M75 34 L 37 66 M75 34 L 75 66 M75 34 L 113 66" fill="none" />
                    <path d="M20 76 L 30 70 M130 76 L 120 70" fill="none" opacity="0.5" />
                  </g>
                  <g>
                    <rect x="61" y="12" width="28" height="26" rx="6" fill="#fdfaf2" stroke="#4a443a" strokeWidth="1.2" />
                    <foreignObject x="65" y="16" width="20" height="20"><SourceIcon source={insp.sourceKey} size={19} /></foreignObject>
                    {(["github", "slack", "notion"] as const).map((s, i) => (
                      <g key={s}>
                        <rect x={23 + i * 38} y={62} width="26" height="24" rx="6" fill="#fdfaf2" stroke="#cfc4a8" strokeWidth="1.1" />
                        <foreignObject x={27 + i * 38} y={66} width="18" height="18"><SourceIcon source={s} size={17} /></foreignObject>
                      </g>
                    ))}
                  </g>
                </svg>
                <Button block style={{ marginTop: 12 }} onClick={() => navigate("/lineage")}>
                  Open in lineage <Ic.ArrowR size={14} />
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

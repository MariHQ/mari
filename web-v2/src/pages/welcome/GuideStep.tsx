// Style-guide step. The Library has no server-side guides query — its guide
// packs are the built-in catalog in Library.tsx (GuidesPanel) with the active
// pack kept in localStorage("mari.styleguide"). This step lists the SAME
// packs (ids must match Library's), plus "Start from scratch", and persists
// the choice both ways: the shared `onboarding` settings key (updateSetting)
// and Library's localStorage convention — so the Library page immediately
// shows the pack you picked here as the project default.

import * as Ic from "../../components/icons";
import { EmptyState } from "../../components/ui";

/** Mirrors GUIDES in src/pages/Library.tsx — same ids, names, rule counts. */
export const GUIDE_PACKS = [
  { id: "plain", name: "Plain language", description: "Direct, concise, and accessible. Prefer familiar words and active voice.", rules: 42 },
  { id: "microsoft", name: "Microsoft", description: "Warm, clear product writing with task-oriented language.", rules: 38 },
  { id: "google", name: "Google developer", description: "Scannable technical guidance with precise, consistent terminology.", rules: 35 },
  { id: "ap", name: "AP style", description: "Newsroom conventions for capitalization, numerals, names, and dates.", rules: 31 },
  { id: "chicago", name: "Chicago", description: "Long-form editorial conventions for careful, polished prose.", rules: 29 },
];

export const SCRATCH_ID = "scratch";

export const guideName = (id: string | null): string | null =>
  id == null ? null : id === SCRATCH_ID ? "Start from scratch" : GUIDE_PACKS.find((g) => g.id === id)?.name ?? id;

export function GuideStep({ guide, saving, onPick, openLibrary }: {
  guide: string | null;
  saving: boolean;
  onPick: (id: string) => void;
  openLibrary: () => void;
}) {
  if (GUIDE_PACKS.length === 0) {
    return (
      <EmptyState
        icon={<Ic.Quill size={22} />}
        title="No style guides yet"
        action={<button className="linklike" onClick={openLibrary}>Open Library → Style guides</button>}
      >
        Create a guide in the Library, then come back — this step lists whatever exists there.
      </EmptyState>
    );
  }

  return (
    <div className="wc-guides" role="radiogroup" aria-label="Style guide">
      {GUIDE_PACKS.map((g) => (
        <label key={g.id} className={`wc-guide${guide === g.id ? " wc-guide--active" : ""}`}>
          <input type="radio" name="wc-guide" checked={guide === g.id} disabled={saving} onChange={() => onPick(g.id)} />
          <span className="wc-guide__mark"><Ic.Quill size={17} /></span>
          <span className="wc-guide__body">
            <b>{g.name}</b>
            <span>{g.description}</span>
          </span>
          <span className="wc-guide__rules">{g.rules}<small>rules</small></span>
          <span className="wc-guide__check">{guide === g.id && <Ic.CheckCircle size={17} />}</span>
        </label>
      ))}
      <label className={`wc-guide wc-guide--scratch${guide === SCRATCH_ID ? " wc-guide--active" : ""}`}>
        <input type="radio" name="wc-guide" checked={guide === SCRATCH_ID} disabled={saving} onChange={() => onPick(SCRATCH_ID)} />
        <span className="wc-guide__mark"><Ic.Pencil size={17} /></span>
        <span className="wc-guide__body">
          <b>Start from scratch</b>
          <span>No pack — build your own rules and project voice in the Library later.</span>
        </span>
        <span className="wc-guide__check">{guide === SCRATCH_ID && <Ic.CheckCircle size={17} />}</span>
      </label>
    </div>
  );
}

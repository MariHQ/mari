// Lineage toolbar — graph search plus the filter/lens/layout/views menus and
// mode toggles. Menus are the design-system Menu (Radix) which owns its own
// open state; the page no longer tracks which menu is open.

import * as Ic from "../../components/icons";
import { SourceIcon } from "../../components/shared";
import {
  Button, Menu, MenuCheckboxItem, MenuItem, MenuRadioGroup, MenuRadioItem,
  fmtDate,
} from "../../components/ui";
import { SourceKey } from "../../data/sources";
import { nodeIcon } from "./glyphs";
import {
  GraphView, LayoutMode, Lens, LENSES, LNode, OWNER_PALETTE, REL, RelKey,
  SOURCE_LABELS, srcKey,
} from "./model";

export type SourceGroup = { source: string; label: string; count: number };

const filterLabel = (all: number, on: number) => (on === all ? "All" : `${on}/${all}`);

export function Toolbar({
  q, onQ, searchResults, inboundOf, onPickSearch,
  sourceGroups, srcOn, onToggleSource,
  relsOn, onToggleRel,
  statusFilter, onStatusFilter,
  lens, onLens,
  layout, onLayout,
  scope, onToggleScope,
  views, onApplyView, onSaveView,
  deriving, onDerive,
  assertOpen, onToggleAssert,
  pathMode, pathPickCount, onTogglePath,
  zoomPct, onZoomBy, onFit,
}: {
  q: string;
  onQ: (v: string) => void;
  searchResults: LNode[];
  inboundOf: (n: LNode) => number;
  onPickSearch: (id: string) => void;
  sourceGroups: SourceGroup[];
  srcOn: (s: string) => boolean;
  onToggleSource: (s: string) => void;
  relsOn: Record<RelKey, boolean>;
  onToggleRel: (k: RelKey) => void;
  statusFilter: "All" | "Warnings";
  onStatusFilter: (s: "All" | "Warnings") => void;
  lens: Lens;
  onLens: (l: Lens) => void;
  layout: LayoutMode;
  onLayout: (l: LayoutMode) => void;
  scope: "focus" | "all";
  onToggleScope: () => void;
  views: GraphView[];
  onApplyView: (v: GraphView) => void;
  onSaveView: () => void;
  deriving: boolean;
  onDerive: () => void;
  assertOpen: boolean;
  onToggleAssert: () => void;
  pathMode: boolean;
  pathPickCount: number;
  onTogglePath: () => void;
  zoomPct: number;
  onZoomBy: (f: number) => void;
  onFit: () => void;
}) {
  const sourcesActive = sourceGroups.filter((r) => srcOn(r.source)).length;
  const relsActive = Object.values(relsOn).filter(Boolean).length;

  return (
    <div className="lineage-toolbar">
      <div className="control-group control-group--wrap">
        {/* search — typeahead over the loaded graph */}
        <div className="lsearch">
          <Ic.Search size={14} />
          <input
            name="lineage-search"
            value={q}
            placeholder="Search the graph…"
            aria-label="Search documents in the graph"
            onChange={(e) => onQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onQ("");
              if (e.key === "Enter" && searchResults[0]) onPickSearch(searchResults[0].id);
            }}
          />
          {q.trim() !== "" && (
            <div className="lsearch__drop">
              {searchResults.length === 0 && <div className="lsearch__empty">No matches in the graph.</div>}
              {searchResults.map((n) => (
                <button key={n.id} className="lsearch__item" onMouseDown={(e) => { e.preventDefault(); onPickSearch(n.id); }}>
                  {nodeIcon(n.icon, n.source, 14)}
                  <span style={{ minWidth: 0 }}>
                    <b>{n.title}</b>
                    <span>{[n.owner, SOURCE_LABELS[srcKey(n.source)], n.date ? fmtDate(n.date) : null].filter(Boolean).join(" · ")}</span>
                  </span>
                  <span className="lsearch__inb">{inboundOf(n)}↩</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {/* Sources */}
        <Menu
          align="start"
          trigger={(
            <Button compact>
              Sources <b className="lg-semibold">{filterLabel(sourceGroups.length, sourcesActive)}</b> <Ic.Chev size={13} />
            </Button>
          )}
        >
          {sourceGroups.map((r) => (
            <MenuCheckboxItem key={r.source} checked={srcOn(r.source)} onCheckedChange={() => onToggleSource(r.source)}>
              <span className="row lg-mrow">
                {r.source === "docsite" ? <Ic.Globe size={14} /> : <SourceIcon source={r.source as SourceKey} size={14} />}
                {r.label} <span className="lg-mcount">({r.count})</span>
              </span>
            </MenuCheckboxItem>
          ))}
        </Menu>
        {/* Relations */}
        <Menu
          align="start"
          trigger={(
            <Button compact>
              Relations <b className="lg-semibold">{filterLabel(Object.keys(REL).length, relsActive)}</b> <Ic.Chev size={13} />
            </Button>
          )}
        >
          {(Object.keys(REL) as RelKey[]).map((k) => (
            <MenuCheckboxItem key={k} checked={relsOn[k]} onCheckedChange={() => onToggleRel(k)}>
              <span className="row lg-mrow">
                <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke={REL[k].color} strokeWidth="2" strokeDasharray={REL[k].dash} /></svg>
                {REL[k].label}
              </span>
            </MenuCheckboxItem>
          ))}
        </Menu>
        {/* Status */}
        <Menu
          align="start"
          trigger={(
            <Button compact>
              Status <b className="lg-semibold">{statusFilter}</b> <Ic.Chev size={13} />
            </Button>
          )}
        >
          <MenuRadioGroup value={statusFilter} onValueChange={(v) => onStatusFilter(v as "All" | "Warnings")}>
            {(["All", "Warnings"] as const).map((s) => (
              <MenuRadioItem key={s} value={s}>{s}</MenuRadioItem>
            ))}
          </MenuRadioGroup>
        </Menu>
        {/* Lens — one at a time */}
        <Menu
          align="start"
          trigger={(
            <Button compact>
              Lens <b className="lg-semibold">{LENSES.find((l) => l.key === lens)!.label}</b> <Ic.Chev size={13} />
            </Button>
          )}
        >
          <MenuRadioGroup value={lens} onValueChange={(v) => onLens(v as Lens)}>
            {LENSES.map((l) => (
              <MenuRadioItem key={l.key} value={l.key}>
                <span className="row lg-mrow">
                  <svg width="22" height="10">
                    {l.key === "source" && <line x1="0" y1="5" x2="22" y2="5" stroke="#1e6fa8" strokeWidth="2" />}
                    {l.key === "stale" && <>{["#2c6e49", "#a05e1c", "#b23a1e"].map((c, i) => <circle key={c} cx={4 + i * 7} cy="5" r="3" fill={c} />)}</>}
                    {l.key === "owner" && <>{OWNER_PALETTE.slice(0, 3).map((c, i) => <circle key={c} cx={4 + i * 7} cy="5" r="3" fill={c} />)}</>}
                    {l.key === "health" && <circle cx="8" cy="5" r="4" fill="#b23a1e" />}
                  </svg>
                  {l.label}
                </span>
              </MenuRadioItem>
            ))}
          </MenuRadioGroup>
        </Menu>
        {/* Layout: dagre flow ↔ timeline preset */}
        <Menu
          align="start"
          trigger={(
            <Button compact>
              Layout <b className="lg-semibold">{layout === "flow" ? "Flow" : layout === "timeline" ? "Timeline" : layout === "circle" ? "Circle" : "Impact clusters"}</b> <Ic.Chev size={13} />
            </Button>
          )}
        >
          <MenuRadioGroup value={layout} onValueChange={(v) => onLayout(v as LayoutMode)}>
            {([["flow", "Flow (left → right)"], ["timeline", "Timeline (x = time)"], ["circle", "Circle (relationships)"], ["cluster", "Impact clusters"]] as [LayoutMode, string][]).map(([k, label]) => (
              <MenuRadioItem key={k} value={k}>{label}</MenuRadioItem>
            ))}
          </MenuRadioGroup>
        </Menu>
        {/* Scope: focal + 1 hop ↔ whole graph */}
        <Button
          compact
          onClick={onToggleScope}
          title={scope === "focus" ? "Showing the focal neighborhood — click for everything" : "Showing everything — click to focus"}
        >
          <Ic.Expand size={13} /> {scope === "focus" ? "Focus" : "Everything"}
        </Button>
        {/* Saved views */}
        <Menu
          align="start"
          trigger={(
            <Button compact>
              <Ic.Bookmark size={13} /> Views <Ic.Chev size={13} />
            </Button>
          )}
        >
          {views.map((v) => (
            <MenuItem key={v.id} icon={<Ic.Bookmark size={14} />} onSelect={() => onApplyView(v)}>
              {v.name}
            </MenuItem>
          ))}
          {views.length === 0 && <div className="lg-hint lg-menu-note">No saved views yet.</div>}
          <MenuItem icon={<Ic.Plus size={14} />} onSelect={onSaveView}>Save current view…</MenuItem>
        </Menu>
        <Button compact className={deriving ? "lg-busy" : undefined} onClick={onDerive} disabled={deriving}>
          <Ic.Sparkle size={14} /> {deriving ? "Mari is reading…" : "Derive links"}
        </Button>
      </div>
      <div className="control-group lineage-toolbar__nav">
        <Button compact className={assertOpen ? "lg-on" : undefined} onClick={onToggleAssert}>
          <Ic.Sparkle size={14} /> Assert impact
        </Button>
        <Button compact className={pathMode ? "lg-on" : undefined} onClick={onTogglePath}>
          <Ic.LineageIcon size={14} /> {pathMode ? (pathPickCount < 2 ? "Pick two nodes… (Esc)" : "Exit path") : "Find path"}
        </Button>
        <div className="control-group control-group--tight" aria-label="Graph zoom controls">
          <Button icon aria-label="Zoom out" onClick={() => onZoomBy(1 / 1.25)}>−</Button>
          <span className="card__hint zoom-readout">{zoomPct}%</span>
          <Button icon aria-label="Zoom in" onClick={() => onZoomBy(1.25)}>＋</Button>
          <Button compact onClick={onFit} title="Fit the graph to the canvas" aria-label="Fit graph">
            <Ic.Expand size={13} /> Fit
          </Button>
        </div>
      </div>
    </div>
  );
}

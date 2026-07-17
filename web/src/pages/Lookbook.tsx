// Design lookbook — your workspace brand plus every canonical primitive,
// exhibited live. This page is itself built ONLY from the primitives it
// documents.
import { ReactNode, useState } from "react";
import {
  Button,
  Card,
  Chip,
  CountChip,
  Drawer,
  EmptyState,
  Field,
  IconRing,
  Input,
  Menu,
  MenuCheckboxItem,
  MenuItem,
  MenuLabel,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  PageHeader,
  Popover,
  Select,
  SeverityChip,
  Spinner,
  Stat,
  StatusChip,
  Stepper,
  Switch,
  Table,
  Tabs,
  Textarea,
  Tooltip,
  ConfirmButton,
  Sparkline,
  useToast,
  fmtDate,
  fmtDateTime,
} from "../components/ui";
import { BrandingEditor } from "./settings/BrandingEditor";
import * as Ic from "../components/icons";
import { Sprig, TexturePlaceholder } from "../components/art";
import { SourceIcon } from "../components/shared";
import "./lookbook.css";

/* Exhibit scaffolding — a Card with the usage rule + import path up top. */
function Exhibit({
  title,
  rule,
  imports,
  children,
}: {
  title: string;
  rule: ReactNode;
  imports: string;
  children: ReactNode;
}) {
  return (
    <Card title={title}>
      <p className="lb-rule">{rule}</p>
      <code className="lb-import">{imports}</code>
      {children}
    </Card>
  );
}

const TOKENS: { name: string; varName: string; hex: string }[] = [
  { name: "Paper", varName: "--paper", hex: "#f6f0e3" },
  { name: "Paper deep", varName: "--paper-deep", hex: "#f1e9d8" },
  { name: "Card", varName: "--card", hex: "#fcf9f1" },
  { name: "Card inner", varName: "--card-inner", hex: "#f7f2e6" },
  { name: "Line", varName: "--line", hex: "#e2d8c2" },
  { name: "Ink", varName: "--ink", hex: "#2d2a22" },
  { name: "Ink soft", varName: "--ink-soft", hex: "#6b6353" },
  { name: "Ink faint", varName: "--ink-faint", hex: "#978d78" },
  { name: "Terracotta", varName: "--terra", hex: "#b04e2c" },
  { name: "Green", varName: "--green", hex: "#5c7a4c" },
  { name: "Blue", varName: "--blue", hex: "#35549d" },
  { name: "Red", varName: "--red", hex: "#bf4f2e" },
  { name: "Gold", varName: "--gold", hex: "#c8973a" },
];

const CHIP_TONES = ["neutral", "green", "blue", "gold", "terra", "red", "ink", "violet", "faint"] as const;

const SAMPLE_ROWS = [
  { doc: "Pricing FAQ", owner: "Priya N.", status: "verified" as const, sev: "low" as const, when: "2026-07-14T09:12:00" },
  { doc: "Onboarding runbook", owner: "Sam K.", status: "stale" as const, sev: "high" as const, when: "2026-05-02T16:40:00" },
  { doc: "Q3 launch brief", owner: "Dana R.", status: "draft" as const, sev: "med" as const, when: "2025-11-20T11:05:00" },
];

function ToastDemo() {
  const toast = useToast();
  return (
    <>
      <Button compact onClick={() => toast("Answer approved and embedded", "success")}>Success toast</Button>
      <Button compact onClick={() => toast("Couldn’t reach Mari — try again", "error")}>Error toast</Button>
    </>
  );
}

export default function LookbookPage() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [bareDrawerOpen, setBareDrawerOpen] = useState(false);
  const [segTab, setSegTab] = useState<"all" | "open" | "done">("all");
  const [filterTab, setFilterTab] = useState<"any" | "verified" | "stale">("any");
  const [underTab, setUnderTab] = useState<"docs" | "facts" | "people">("docs");
  const [step, setStep] = useState(1);
  const [digest, setDigest] = useState(true);
  const [showStale, setShowStale] = useState(true);
  const [sort, setSort] = useState("recent");
  const [chipOn, setChipOn] = useState(true);

  return (
    <div className="lb">
      <PageHeader
        eyebrow="Design system"
        title="Design lookbook"
        description="Your workspace brand, then every canonical card, chip, menu, and control — with the usage rule and import path. Pages compose these primitives; zero bespoke cards, menus, or chips."
        actions={<Button variant="primary"><Ic.Sparkle size={15} /> Primary action</Button>}
      />

      {/* ————— Your brand ————— */}
      <Exhibit
        title="Your brand"
        rule={<><b>The tokens below are your brand</b> — edit them here; every exhibit re-skins live. Workspace branding re-points the :root custom properties — accent, greens, gold, washes, --display — and every primitive re-inks itself. Pages never change; that constraint is the proof of the design system. Never hardcode a palette hex: use the token (or add a semantic one) and the brand swap is free.</>}
        imports={'import { useBranding, computeCssVars } from "../lib/branding"'}
      >
        <BrandingEditor />
      </Exhibit>

      {/* ————— Voice & tokens ————— */}
      <Card title="Voice & tokens" icon={<IconRing><Ic.Quill size={15} /></IconRing>} hint="src/styles.css :root">
        <p className="lb-rule">
          Editorial notebook: cream paper, bookish serifs, hand-hatched textures, ink lines. <b>Playfair Display</b> for
          display headings, <b>Lora</b> for body prose, <b>Source Sans 3</b> for meta/labels. Spacing rhythm: 16px card
          gutters, 18px card inset, 12–14px inner gaps. Every surface carries the paper texture
          (<code>--paper-texture</code>) and a wobble border-radius — never a perfect rectangle.
        </p>
        <div className="lb-swatches">
          {TOKENS.map((t) => (
            <figure className="lb-swatch" key={t.varName}>
              <div className="lb-swatch__color" style={{ background: `var(${t.varName})` }} />
              <figcaption>
                <b>{t.name}</b>
                <code>{t.varName} · {t.hex}</code>
              </figcaption>
            </figure>
          ))}
        </div>
        <div className="lb-grid2" style={{ marginTop: 16 }}>
          <div className="lb-type">
            <div><small>Display · Playfair 31px</small><span style={{ font: "500 31px/1.05 var(--display)", color: "var(--ink-strong)" }}>Knowledge, kept honest</span></div>
            <div><small>Card title · 18.5px</small><span style={{ font: "600 18.5px var(--display)", color: "var(--ink-strong)" }}>Source pulse</span></div>
            <div><small>Body · Lora 15px</small><span style={{ font: "15px/1.45 var(--serif)" }}>The team's answers, decisions, and facts on one sheet of paper.</span></div>
            <div><small>Meta · Source Sans 12.5px</small><span style={{ font: "12.5px var(--sans)", color: "var(--ink-soft)" }}>Updated {fmtDate("2026-07-14")} · 12 sources</span></div>
            <div><small>Section label</small><span className="field__label">Uppercase 10.5px / 0.12em</span></div>
          </div>
          <div className="lb-row lb-row--top">
            <div className="lb-wobble">wobble radius<br />13 10 14 11 / 11 14 10 13</div>
            <div className="lb-wobble" style={{ borderStyle: "dashed", borderColor: "#c7b794" }}>dashed border<br />= editable / dropzone</div>
            <div className="lb-wobble" style={{ boxShadow: "var(--shadow)", borderColor: "var(--line)" }}>ink shadow<br />--shadow</div>
          </div>
        </div>
      </Card>

      {/* ————— Libraries ————— */}
      <Card title="Libraries" icon={<IconRing><Ic.Book size={15} /></IconRing>}>
        <ul className="lb-libs">
          <li><b>Radix UI primitives</b> — <span>menus, drawers, tabs, tooltips, switches: accessible behavior for free, skinned in-house with the notebook look.</span></li>
          <li><b>cytoscape + dagre</b> — <span>lineage/flow graphs; the only canvas-y dependency.</span></li>
          <li><b>clsx</b> — <span>class composition in primitives.</span></li>
          <li><b>No Tailwind</b> — <span>one hand-tuned styles.css keeps the hand-sketched voice consistent; utilities would fight the wobble.</span></li>
        </ul>
      </Card>

      {/* ————— Cards ————— */}
      <Exhibit
        title="Cards"
        rule={<>The Card owns its padding — pages never inset card content themselves. <b>default</b> 16/18, <b>plain</b> roomy 20/22 for prose, <b>flush</b> 0 for tables/lists that manage their own gutters. Head slots: icon, eyebrow, title, hint, actions.</>}
        imports={'import { Card } from "../components/ui"'}
      >
        <div className="lb-grid3">
          <Card title="Default card" icon={<IconRing tone="green"><Ic.Leaf size={15} /></IconRing>} hint="hint slot" actions={<Menu trigger={<Button icon compact aria-label="Card menu"><Ic.Kebab size={15} /></Button>}><MenuItem icon={<Ic.Pencil size={14} />}>Rename</MenuItem><MenuItem danger icon={<Ic.Trash size={14} />}>Delete</MenuItem></Menu>}>
            Body content sits on the canonical 16px 18px inset, 12px under a head.
          </Card>
          <Card variant="plain" title="Plain card" eyebrow="Eyebrow">
            Roomier 20px 22px padding for settings panels and prose-heavy sections.
          </Card>
          <Card variant="flush" title="Flush card">
            <Table columns={["Doc", "Status"]}>
              <tr><td>Pricing FAQ</td><td><StatusChip status="verified" /></td></tr>
              <tr><td>Runbook</td><td><StatusChip status="stale" /></td></tr>
            </Table>
          </Card>
        </div>
      </Exhibit>

      {/* ————— Page headers ————— */}
      <Exhibit
        title="Page headers"
        rule={<>One header per page, terracotta underline is its signature. Consolidates the old .sethead, .kb__hero, .chat__title, and bare .page-title idioms. Props: title, eyebrow, description, icon (sprig default), actions, backLink.</>}
        imports={'import { PageHeader } from "../components/ui"'}
      >
        <PageHeader
          eyebrow="Knowledge"
          title="Approved answers"
          description="Questions the team has settled, with sources and owners."
          backLink={{ to: "/lookbook", label: "Back to overview" }}
          actions={<><Button compact><Ic.Refresh size={14} /> Refresh</Button><Button variant="primary" compact><Ic.Plus size={14} /> New answer</Button></>}
        />
      </Exhibit>

      {/* ————— Buttons ————— */}
      <Exhibit
        title="Buttons"
        rule={<><b>Primary = terracotta, one per view.</b> Success green only for confirm/verified contexts. Danger for destructive. Compact in toolbars/card heads; icon needs an aria-label; block for wizard/drawer footers; link for inline navigation-ish actions.</>}
        imports={'import { Button } from "../components/ui"'}
      >
        <div className="lb-row">
          <span className="lb-label">Variants</span>
          <Button variant="primary"><Ic.Plus size={15} /> Primary</Button>
          <Button>Default</Button>
          <Button variant="success"><Ic.Check size={15} /> Success</Button>
          <Button variant="danger"><Ic.Trash size={15} /> Danger</Button>
          <Button variant="link">Link button <Ic.ArrowR size={14} /></Button>
        </div>
        <div className="lb-row">
          <span className="lb-label">Modifiers</span>
          <Button variant="primary" compact>Compact primary</Button>
          <Button compact>Compact</Button>
          <Tooltip label="Icon buttons always get an aria-label">
            <Button icon aria-label="Settings"><Ic.Gear size={15} /></Button>
          </Tooltip>
          <Button icon compact aria-label="More"><Ic.Kebab size={15} /></Button>
          <Button disabled>Disabled</Button>
          <Button compact disabled><Spinner size="sm" /> Saving…</Button>
        </div>
        <div className="lb-row">
          <span className="lb-label">Block</span>
          <div style={{ width: 320 }}>
            <Button variant="primary" block>Continue <Ic.ArrowR size={15} /></Button>
          </div>
        </div>
      </Exhibit>

      {/* ————— Chips & pills ————— */}
      <Exhibit
        title="Chips & pills"
        rule={<>One chip system — replaces .pill, .tag-chip, .dec-sev, .rule-severity, answers .chip. <b>StatusChip</b> for lifecycle, <b>SeverityChip</b> for triage, tone chips for tags, <b>CountChip</b> for numerics (no vertical-offset hacks). onClick makes any chip interactive; selected shows the double-ring.</>}
        imports={'import { Chip, StatusChip, SeverityChip, CountChip } from "../components/ui"'}
      >
        <div className="lb-row">
          <span className="lb-label">Status</span>
          <StatusChip status="verified" />
          <StatusChip status="canonical" />
          <StatusChip status="approved" />
          <StatusChip status="stale" />
          <StatusChip status="draft" />
          <StatusChip status="needs-review" />
          <StatusChip status="running" />
          <StatusChip status="failed" />
          <StatusChip status="retired" />
        </div>
        <div className="lb-row">
          <span className="lb-label">Severity</span>
          <SeverityChip level="high" />
          <SeverityChip level="med" />
          <SeverityChip level="low" />
        </div>
        <div className="lb-row">
          <span className="lb-label">Tags (tones)</span>
          {CHIP_TONES.map((tone) => <Chip key={tone} tone={tone} dot>{tone}</Chip>)}
        </div>
        <div className="lb-row">
          <span className="lb-label">Interactive · count · removable</span>
          <Chip tone="green" dot selected={chipOn} onClick={() => setChipOn(!chipOn)} aria-pressed={chipOn}>Click to {chipOn ? "unselect" : "select"}</Chip>
          <CountChip value={12} />
          <CountChip value="99+" tone="terra" />
          <Chip tone="blue" onRemove={() => undefined} removeLabel="Remove tag">removable</Chip>
        </div>
      </Exhibit>

      {/* ————— Menus & popovers ————— */}
      <Exhibit
        title="Menus & popovers"
        rule={<>One menu (Radix DropdownMenu, notebook skin) — outside-click, Escape, and arrow keys come free. Checkbox/radio items replace MenuChoice. <b>Popover</b> is for content cards on click; <b>Tooltip</b> for hover hints. No more bespoke .template-menu / Drop implementations.</>}
        imports={'import { Menu, MenuItem, MenuCheckboxItem, MenuRadioGroup, MenuRadioItem, Popover, Tooltip } from "../components/ui"'}
      >
        <div className="lb-row">
          <Menu trigger={<Button compact>Actions <Ic.Chev size={14} /></Button>}>
            <MenuItem icon={<Ic.Pencil size={14} />} end={<Ic.ArrowR size={13} />}>Edit answer</MenuItem>
            <MenuItem icon={<Ic.Clipboard size={14} />}>Copy link</MenuItem>
            <MenuSeparator />
            <MenuItem danger icon={<Ic.Trash size={14} />}>Delete</MenuItem>
          </Menu>
          <Menu trigger={<Button compact>Filters <Ic.Chev size={14} /></Button>}>
            <MenuLabel>Show</MenuLabel>
            <MenuCheckboxItem checked={showStale} onCheckedChange={setShowStale}>Stale docs</MenuCheckboxItem>
            <MenuSeparator />
            <MenuLabel>Sort by</MenuLabel>
            <MenuRadioGroup value={sort} onValueChange={setSort}>
              <MenuRadioItem value="recent">Most recent</MenuRadioItem>
              <MenuRadioItem value="owner">Owner</MenuRadioItem>
            </MenuRadioGroup>
          </Menu>
          <Popover trigger={<Button compact>Who verified? <Ic.Chev size={14} /></Button>}>
            <b style={{ font: "600 13.5px var(--serif)" }}>Priya Natarajan</b>
            <div style={{ color: "var(--ink-faint)", marginTop: 3 }}>Verified {fmtDate("2026-07-12")} · 3 sources checked</div>
          </Popover>
          <Tooltip label="Tooltips are for short hover hints only">
            <Button icon aria-label="Hover me"><Ic.Sparkle size={15} /></Button>
          </Tooltip>
        </div>
      </Exhibit>

      {/* ————— Drawers ————— */}
      <Exhibit
        title="Drawers"
        rule={<>One drawer — Radix Dialog, slides from the right at min(400px, 92vw), dashed head border, scrolling body, sticky footer slot. <code>backdrop=false</code> keeps the page interactive for inspector panels.</>}
        imports={'import { Drawer } from "../components/ui"'}
      >
        <div className="lb-row">
          <Button variant="primary" compact onClick={() => setDrawerOpen(true)}>Open drawer</Button>
          <Button compact onClick={() => setBareDrawerOpen(true)}>Open without backdrop</Button>
        </div>
        <Drawer
          open={drawerOpen}
          onOpenChange={setDrawerOpen}
          title="Run · Weekly digest"
          meta={fmtDateTime("2026-07-16T14:17:00")}
          footer={<><Button variant="primary" compact>Re-run flow</Button><Button compact onClick={() => setDrawerOpen(false)}>Close</Button></>}
        >
          <div className="lb-row"><StatusChip status="running" /><SeverityChip level="low" /></div>
          <p style={{ font: "14px/1.5 var(--serif)", margin: "10px 0" }}>
            Drawer body scrolls independently; compose Fields, Tables, and Chips in here.
          </p>
          <Field label="Notify channel"><Input defaultValue="#product-updates" /></Field>
        </Drawer>
        <Drawer open={bareDrawerOpen} onOpenChange={setBareDrawerOpen} title="Inspector" backdrop={false}>
          <EmptyState icon={<Ic.Eye size={22} />}>No backdrop — the page behind stays interactive.</EmptyState>
        </Drawer>
      </Exhibit>

      {/* ————— Tabs ————— */}
      <Exhibit
        title="Tabs"
        rule={<><b>seg</b> (default) for page-level view switching; <b>filter</b> chip-row for filtering a list in place (the one filter-chip idiom — .ans-filters and .template-filters die); <b>underline</b> for dense editorial headers.</>}
        imports={'import { Tabs } from "../components/ui"'}
      >
        <Tabs
          ariaLabel="Seg demo"
          value={segTab}
          onChange={setSegTab}
          options={[{ id: "all", label: "All", count: 24 }, { id: "open", label: "Open", count: 6 }, { id: "done", label: "Done" }]}
        />
        <Tabs
          ariaLabel="Filter demo"
          variant="filter"
          value={filterTab}
          onChange={setFilterTab}
          options={[{ id: "any", label: "Any status" }, { id: "verified", label: "Verified", count: 18 }, { id: "stale", label: "Stale", count: 3 }]}
        />
        <Tabs
          ariaLabel="Underline demo"
          variant="underline"
          value={underTab}
          onChange={setUnderTab}
          options={[{ id: "docs", label: "Documents", count: 132 }, { id: "facts", label: "Facts", count: 41 }, { id: "people", label: "People" }]}
        />
      </Exhibit>

      {/* ————— Steppers ————— */}
      <Exhibit
        title="Steppers"
        rule={<>The 33px-dot wizard stepper is the only stepper. Omit onSelect for a read-only progress strip. The auth-steps and welcome-local variants are retired in migration.</>}
        imports={'import { Stepper } from "../components/ui"'}
      >
        <Stepper labels={["Connect sources", "Set baseline", "Invite team", "Finish"]} current={step} onSelect={setStep} />
      </Exhibit>

      {/* ————— Stats ————— */}
      <Exhibit
        title="Stats"
        rule={<>Big display number + label + toned sub note. Pass onClick for the click-to-filter affordance (stat strips above lists). Optional texture swatch and icon ring.</>}
        imports={'import { Stat } from "../components/ui"'}
      >
        <div className="lb-grid3">
          <Stat value={132} label="Documents indexed" sub="+8 this week" tone="green" swatch={<TexturePlaceholder hue={30} />} />
          <Stat value={7} label="Stale answers" sub="needs review" tone="red" ring={<IconRing tone="red" size={28}><Ic.Bell size={14} /></IconRing>} onClick={() => setFilterTab("stale")} />
          <Stat value="98%" label="Fact-check pass rate" sub="last 30 days" tone="blue" />
        </div>
      </Exhibit>

      {/* ————— Tables ————— */}
      <Exhibit
        title="Tables"
        rule={<>Generic .table look with consistent gutters, wrapped for horizontal overflow. Always inside <code>Card variant="flush"</code>. Dates use fmtDate; timestamps only in feeds.</>}
        imports={'import { Table } from "../components/ui"'}
      >
        <Card variant="flush">
          <Table columns={["Document", "Owner", "Status", "Severity", { label: "Updated", width: 110 }]}>
            {SAMPLE_ROWS.map((r) => (
              <tr key={r.doc}>
                <td style={{ font: "600 14px var(--serif)" }}>{r.doc}</td>
                <td>{r.owner}</td>
                <td><StatusChip status={r.status} /></td>
                <td><SeverityChip level={r.sev} /></td>
                <td style={{ color: "var(--ink-faint)", fontFamily: "var(--sans)", fontSize: 12.5 }}>{fmtDate(r.when)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </Exhibit>

      {/* ————— Forms ————— */}
      <Exhibit
        title="Forms"
        rule={<>Field wraps the uppercase 10.5px/0.12em label + control + hint. Input/Select/Textarea carry the canonical .input skin. Switch is the Radix toggle. The green .saved-note flashes after saves.</>}
        imports={'import { Field, Input, Select, Textarea, Switch, SectionLabel } from "../components/ui"'}
      >
        <div className="lb-grid2">
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Field label="Workspace name" hint="Shown in digests and published sites.">
              <Input placeholder="Your workspace name" />
            </Field>
            <Field label="Digest cadence">
              <Select defaultValue="weekly">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </Select>
            </Field>
            <div className="lb-row">
              <Switch checked={digest} onCheckedChange={setDigest} label="Send Monday digest" />
              <span className="saved-note"><Ic.Check size={13} /> Saved</span>
            </div>
          </div>
          <Field label="Voice & tone" hint="Feeds the editorial detector.">
            <Textarea defaultValue="Plain, direct, no hype. Contractions welcome." />
          </Field>
        </div>
      </Exhibit>

      {/* ————— Empty states & spinners ————— */}
      <div className="lb-grid2">
        <Exhibit
          title="Empty states"
          rule={<>One empty style: centered italic serif, optional icon and action. No page-local variants.</>}
          imports={'import { EmptyState } from "../components/ui"'}
        >
          <EmptyState
            icon={<Sprig size={34} />}
            title="No stale answers"
            action={<Button variant="link">Review the queue <Ic.ArrowR size={13} /></Button>}
          >
            Everything the team relies on has been checked this month.
          </EmptyState>
        </Exhibit>
        <Exhibit
          title="Spinners"
          rule={<>One animation, two sizes: sm (13px) inline/buttons, md (22px) panel loads.</>}
          imports={'import { Spinner } from "../components/ui"'}
        >
          <div className="lb-row">
            <Spinner size="sm" />
            <Spinner size="md" />
            <Button compact disabled><Spinner size="sm" /> Syncing…</Button>
          </div>
          <div className="lb-row">
            <span className="lb-label">Date formats — ui/format.ts</span>
            <Chip tone="ink">fmtDate → {fmtDate("2026-07-16")}</Chip>
            <Chip tone="ink">other year → {fmtDate("2025-03-04")}</Chip>
            <Chip tone="ink">fmtDateTime (feeds only) → {fmtDateTime("2026-07-16T14:17:00")}</Chip>
          </div>
        </Exhibit>
      </div>

      {/* ————— Toasts, confirms, sparklines ————— */}
      <div className="lb-grid2">
        <Exhibit
          title="Toasts & confirms"
          rule={<>Transient feedback via <code>useToast()</code> — one skin, bottom-right, three tones; <code>Toaster</code> mounts once in the shell. Destructive actions use <code>ConfirmButton</code>: first click arms, second within 4s fires. No <code>window.confirm</code>, no page-local confirm rows.</>}
          imports={'import { useToast, ConfirmButton } from "../components/ui"'}
        >
          <div className="lb-row">
            <ToastDemo />
            <ConfirmButton compact confirmLabel="Really revoke?" onConfirm={() => {}}>
              <Ic.Trash size={13} /> Revoke key
            </ConfirmButton>
          </div>
        </Exhibit>
        <Exhibit
          title="Sparklines"
          rule={<>Small trend readouts (served/wk, source pulse). One implementation, chip-palette tones — never restyle inline.</>}
          imports={'import { Sparkline } from "../components/ui"'}
        >
          <div className="lb-row">
            <Sparkline values={[2, 5, 3, 8, 6, 9, 12]} />
            <Sparkline values={[9, 7, 8, 5, 6, 4, 3]} tone="terra" />
            <Sparkline values={[1, 2, 2, 3, 5, 5, 8]} tone="blue" />
          </div>
        </Exhibit>
      </div>

      {/* ————— Feed rows, source cards, avatars ————— */}
      <div className="lb-grid2">
        <Exhibit
          title="Feed / list rows"
          rule={<>Activity rows: 26px icon ring, one-line serif text, sans timestamp right-aligned (fmtDateTime). List rows keep 6–8px side inset inside flush cards — never 2px from the border.</>}
          imports={'classes: .live__row · ring via <IconRing size={26}>'}
        >
          <Card variant="flush">
            <div className="live__list" style={{ margin: "4px 10px 8px" }}>
              {[
                { icon: <Ic.CheckCircle size={14} />, text: <>Priya verified <span className="live__target">Pricing FAQ</span></>, at: "2026-07-16T09:12:00" },
                { icon: <Ic.Pencil size={14} />, text: <>Sam edited <span className="live__target">Onboarding runbook</span></>, at: "2026-07-15T17:40:00" },
                { icon: <Ic.Send size={14} />, text: <>Digest sent to <span className="live__target">#product</span></>, at: "2026-07-14T09:00:00" },
              ].map((row, i) => (
                <div className="live__row" key={i}>
                  <span className="live__icon">{row.icon}</span>
                  <span className="live__text">{row.text}</span>
                  <span className="live__at">{fmtDateTime(row.at)}</span>
                </div>
              ))}
            </div>
          </Card>
        </Exhibit>
        <Exhibit
          title="Source cards & avatars"
          rule={<>Citation cards: numbered ring, source mark, name + date on the first line, tags pinned to the bottom. Avatars: 38px (26px --sm) with four tints.</>}
          imports={'classes: .srccard · .avatar .avatar--tintN'}
        >
          <div className="lb-row lb-row--top">
            <div className="srccard" style={{ width: 250 }}>
              <div className="srccard__top">
                <span className="srccard__n">1</span>
                <SourceIcon source="slack" size={17} />
                <span className="srccard__id">
                  <span className="srccard__src">#pricing thread</span>
                  <span className="srccard__meta">Slack · 14 replies</span>
                </span>
                <span className="srccard__date"><Ic.Clock size={11} /> {fmtDate("2026-07-12")}</span>
              </div>
              <span className="srccard__text">"Enterprise tier includes SSO as of the July release."</span>
              <span className="srccard__by">Priya N.</span>
              <div className="srccard__tags"><Chip tone="green" dot>canonical</Chip></div>
            </div>
            <div className="lb-row" style={{ flexDirection: "column", alignItems: "flex-start" }}>
              <div className="lb-row">
                <span className="avatar avatar--tint1">PN</span>
                <span className="avatar avatar--tint2">SK</span>
                <span className="avatar avatar--tint3">DR</span>
                <span className="avatar avatar--tint4">JW</span>
                <span className="avatar avatar--sm avatar--tint1">PN</span>
              </div>
              <div className="lb-row">
                <IconRing><Ic.Book size={15} /></IconRing>
                <IconRing tone="green"><Ic.Check size={15} /></IconRing>
                <IconRing tone="red"><Ic.Bell size={15} /></IconRing>
                <IconRing tone="blue"><Ic.ShieldCheck size={15} /></IconRing>
                <IconRing tone="gold"><Ic.Sparkle size={15} /></IconRing>
              </div>
            </div>
          </div>
        </Exhibit>
      </div>
    </div>
  );
}

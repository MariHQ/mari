import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as Ic from "../components/icons";
import { TagChip } from "../components/TagChip";
import {
  Button,
  Card,
  ConfirmButton,
  EmptyState,
  Field,
  Input,
  Menu,
  MenuItem,
  PageHeader,
  Select,
  Spinner,
  Tabs,
  Textarea,
} from "../components/ui";
import { QueryResult, gql, useQuery } from "../lib/api";
import GlossaryPanel, { GLOSSARY_QUERY, GlossaryTerm, mapGlossary } from "./library/GlossaryPanel";
import RulesPanel, { RULE_CATALOG_COUNT } from "./library/RulesPanel";
import { useSettings } from "./settings/shared";
import "./library.css";

type LibraryTab = "tags" | "rules" | "guides" | "glossary" | "templates";
type TagDefinition = {
  id: string;
  name: string;
  description: string;
  tone: string;
  kind: string;
  weight: number;
  usage: number;
  evidence: string;
  behaviors: string[];
  standard?: boolean;
};

// Pill style key (API `kind`) ↔ display tone.
const KIND_TONE: Record<string, string> = { canonical: "green", verified: "blue", approval: "blue", factcheck: "gold", stale: "red", neutral: "ink" };
const TONE_KIND: Record<string, string> = { green: "canonical", blue: "verified", gold: "factcheck", red: "stale", ink: "neutral" };
const KIND_EVIDENCE: Record<string, string> = {
  canonical: "Preferred evidence",
  verified: "Trusted evidence",
  approval: "Strict fact check",
  factcheck: "Surface in audits",
  stale: "Flagged, not evidence",
  neutral: "Normal evidence",
};
const DEFAULT_TAG_DESCRIPTIONS: Record<string, string> = {
  canonical: "The current source of truth.",
  verified: "Facts extracted from it are trusted.",
  "customer-facing": "Approved for external and published surfaces.",
  "internal-only": "Useful to the team, not for customer surfaces.",
  "needs-review": "Flagged for a person to make a judgment.",
  stale: "Known to be out of date and ready for review.",
  deprecated: "Superseded by a newer document.",
  draft: "Work in progress that is not trusted yet.",
};

const TAG_DESC_KEY = "mari.tagDescriptions";
function loadTagDescriptions(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(TAG_DESC_KEY) || "{}"); } catch { return {}; }
}
function saveTagDescription(tag: string, description: string) {
  try {
    const map = loadTagDescriptions();
    map[tag] = description;
    localStorage.setItem(TAG_DESC_KEY, JSON.stringify(map));
  } catch { /* storage unavailable — description stays session-local */ }
}

const TAGDEFS_QUERY = `{ tagDefs { tag label kind searchWeight isDefault behaviors } }`;
function mapTagDefs(data: any): TagDefinition[] {
  const descriptions = loadTagDescriptions();
  return (data.tagDefs as any[]).map((def) => {
    const kind: string = def.kind || "neutral";
    return {
      id: def.tag,
      name: def.label,
      description: descriptions[def.tag] || DEFAULT_TAG_DESCRIPTIONS[def.tag] || "Custom team label.",
      tone: KIND_TONE[kind] || "ink",
      kind,
      weight: def.searchWeight ?? 1,
      usage: 0,
      evidence: KIND_EVIDENCE[kind] || "Normal evidence",
      behaviors: def.behaviors ? String(def.behaviors).split(";").map((item: string) => item.trim()).filter(Boolean) : ["Custom label"],
      standard: !!def.isDefault,
    };
  });
}

const UPSERT_TAG = `mutation($tag: String!, $label: String!, $kind: String, $searchWeight: Float, $behaviors: String) {
  upsertTagDef(tag: $tag, label: $label, kind: $kind, searchWeight: $searchWeight, behaviors: $behaviors)
}`;

const GUIDES = [
  { id: "plain", name: "Plain language", description: "Direct, concise, and accessible. Prefer familiar words and active voice.", rules: 42, tone: "green" },
  { id: "microsoft", name: "Microsoft", description: "Warm, clear product writing with task-oriented language.", rules: 38, tone: "blue" },
  { id: "google", name: "Google developer", description: "Scannable technical guidance with precise, consistent terminology.", rules: 35, tone: "gold" },
  { id: "ap", name: "AP style", description: "Newsroom conventions for capitalization, numerals, names, and dates.", rules: 31, tone: "terra" },
  { id: "chicago", name: "Chicago", description: "Long-form editorial conventions for careful, polished prose.", rules: 29, tone: "violet" },
];

const GUIDE_RULES: Record<string, string[]> = {
  plain: ["Prefer words of one or two syllables when they carry the meaning", "Keep sentences under 25 words", "Address the reader as “you”", "Use active voice unless the actor is unknown"],
  microsoft: ["Use contractions for a natural tone", "Drop “please” from direct instructions", "Use sentence case everywhere, including titles", "Bold UI labels exactly as they appear in the product"],
  google: ["Write in second person, present tense", "Put conditions before instructions", "Use numbered steps for sequences, bullets for options", "Define an initialism at first mention"],
  ap: ["Spell out numbers one through nine, numerals for 10 and above", "Abbreviate months with specific dates", "Use last names on second reference", "No serial comma in simple series"],
  chicago: ["Use the serial comma", "Spell out numbers up to one hundred", "Italicize titles of standalone works", "Lowercase job titles unless they precede a name"],
};

const TEMPLATES = [
  { id: "runbook", name: "Runbook", category: "Operations", description: "Service overview, alarms, diagnosis, rollback, escalation.", sections: 8, standard: true, icon: Ic.Clipboard },
  { id: "adr", name: "Architecture decision", category: "Engineering", description: "Context, decision, alternatives, and consequences.", sections: 6, standard: true, icon: Ic.Fork },
  { id: "postmortem", name: "Postmortem", category: "Operations", description: "Impact, timeline, root cause, response, and follow-ups.", sections: 9, standard: true, icon: Ic.ShieldCheck },
  { id: "rfc", name: "RFC", category: "Engineering", description: "Problem, goals, proposal, risks, and rollout plan.", sections: 7, standard: true, icon: Ic.Doc },
  { id: "onboarding", name: "Onboarding guide", category: "Team", description: "Context, first-week checklist, people, tools, and milestones.", sections: 7, standard: true, icon: Ic.Sprout },
  { id: "api", name: "API reference page", category: "Product", description: "Endpoint purpose, request, response, errors, and examples.", sections: 6, standard: true, icon: Ic.Book },
  { id: "release", name: "Release notes", category: "Product", description: "What changed, who it helps, migration notes, and links.", sections: 5, standard: true, icon: Ic.Megaphone },
  { id: "security", name: "Security policy", category: "Governance", description: "Supported versions, reporting, disclosure, and response.", sections: 6, standard: true, icon: Ic.ShieldCheck },
];

const TEMPLATE_SECTIONS: Record<string, string[]> = {
  runbook: ["Service overview", "Architecture and dependencies", "Alarms and thresholds", "Dashboards", "Diagnosis steps", "Mitigation", "Rollback procedure", "Escalation contacts"],
  adr: ["Context", "Decision", "Alternatives considered", "Consequences", "Rollout", "References"],
  postmortem: ["Summary", "Customer impact", "Timeline", "Root cause", "Detection", "Response", "Recovery", "Lessons learned", "Follow-up actions"],
  rfc: ["Problem statement", "Goals", "Non-goals", "Proposal", "Risks and mitigations", "Rollout plan", "Open questions"],
  onboarding: ["Welcome and context", "First-week checklist", "People to meet", "Tools and access", "Codebase tour", "First tasks", "30/60/90 milestones"],
  api: ["Endpoint purpose", "Authentication", "Request", "Response", "Errors", "Examples"],
  release: ["Highlights", "What changed", "Who it helps", "Migration notes", "Links"],
  security: ["Supported versions", "Reporting a vulnerability", "Disclosure policy", "Response targets", "Scope", "Safe harbor"],
};
const GENERIC_SECTIONS = ["Overview", "Context", "Details", "Process", "Risks", "Review", "References", "Appendix", "Next steps", "Owners", "Changelog", "Links"];

type CustomTemplate = { id: string; name: string; category: string; description: string; sections: number };
const TEMPLATES_KEY = "mari.templates";
function loadCustomTemplates(): CustomTemplate[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(TEMPLATES_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

type SearchDoc = { id: number | string; tags: string[] };

function TagsPanel({ tagsQ }: { tagsQ: QueryResult<TagDefinition[]> }) {
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [weightId, setWeightId] = useState<string | null>(null);
  const [weightDraft, setWeightDraft] = useState("1.0");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ name: "", description: "", tone: "blue", weight: "1.0" });
  const docsQ = useQuery<SearchDoc[]>(`{ search(query: "", k: 200) { id tags } }`, { map: (data: any) => data.search ?? [] });
  const docs = docsQ.data ?? [];
  const tags = useMemo(() => tagsQ.data ?? [], [tagsQ.data]);
  const refetch = () => {
    tagsQ.refetch();
    docsQ.refetch();
  };
  const visible = useMemo(() => tags.filter((tag) => `${tag.name} ${tag.description} ${tag.behaviors.join(" ")}`.toLowerCase().includes(query.toLowerCase())), [tags, query]);

  const usageOf = (id: string) => (docs.length ? docs.filter((doc) => doc.tags?.includes(id)).length : tags.find((tag) => tag.id === id)?.usage ?? 0);
  const taggedDocs = docs.filter((doc) => doc.tags?.length).length;
  const coveragePct = docs.length ? Math.round((taggedDocs / docs.length) * 100) : null;
  const needCuration = docs.length ? docs.length - taggedDocs + docs.filter((doc) => doc.tags?.includes("needs-review")).length : null;
  const rankingRules = tags.filter((tag) => tag.weight !== 1).length;

  const openComposer = (tag?: TagDefinition) => {
    setAdding(true);
    setEditingId(tag ? tag.id : null);
    setDraft(tag
      ? { name: tag.name, description: tag.description, tone: tag.tone, weight: String(tag.weight) }
      : { name: "", description: "", tone: "blue", weight: "1.0" });
  };

  const saveTag = async () => {
    const name = draft.name.trim();
    if (!name || busy) return;
    setBusy(true);
    const tag = editingId ?? name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    const existing = editingId ? tags.find((item) => item.id === editingId) : undefined;
    await gql(UPSERT_TAG, {
      tag,
      label: name,
      kind: TONE_KIND[draft.tone] || "neutral",
      searchWeight: Number(draft.weight) || 1,
      behaviors: existing ? existing.behaviors.join("; ") : "Custom label",
    });
    if (draft.description.trim()) saveTagDescription(tag, draft.description.trim());
    setBusy(false);
    setAdding(false);
    setEditingId(null);
    setDraft({ name: "", description: "", tone: "blue", weight: "1.0" });
    refetch();
  };

  const removeTag = async (tag: TagDefinition) => {
    await gql(`mutation($tag: String!) { deleteTagDef(tag: $tag) }`, { tag: tag.id });
    refetch();
  };

  const commitWeight = async (tag: TagDefinition) => {
    const weight = Number(weightDraft);
    setWeightId(null);
    if (!Number.isFinite(weight) || weight < 0 || weight === tag.weight) return;
    await gql(`mutation($tag: String!, $weight: Float!) { setTagWeight(tag: $tag, weight: $weight) }`, { tag: tag.id, weight });
    refetch();
  };

  return (
    <div className="library-panel" role="tabpanel">
      <div className="library-stats" aria-label="Tag health">
        <div><strong>{tagsQ.data ? tags.length : "—"}</strong><span>active definitions</span></div>
        <div><strong>{coveragePct !== null ? `${coveragePct}%` : "—"}</strong><span>documents tagged</span></div>
        <div><strong>{needCuration !== null ? needCuration : "—"}</strong><span>need curation</span></div>
        <div><strong>{tagsQ.data ? rankingRules : "—"}</strong><span>ranking rules</span></div>
      </div>

      <Card>
        <div className="sectionbar">
          <div>
            <h3>Tag definitions</h3>
            <p>One vocabulary controls ranking, evidence, publishing, audits, and workflows.</p>
          </div>
          <div className="sectionbar__actions">
            <label className="compact-search"><Ic.Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a tag" /></label>
            <Button compact onClick={() => setAnalyzing((value) => !value)}><Ic.Sparkle size={14} /> Analyze documents</Button>
            <Button variant="primary" compact onClick={() => openComposer()}><Ic.Plus size={14} /> New tag</Button>
          </div>
        </div>

        {analyzing && (
          <div className="curation-queue">
            <div className="curation-queue__head">
              <div><span className="eyebrow">Corpus analysis</span><h4>Tag coverage across indexed documents</h4></div>
              <span>{docs.length ? `${docs.length} documents · live from search` : "search API unreachable"}</span>
            </div>
            {docs.length ? (
              <div className="tag-coverage">
                {[...tags].sort((a, b) => usageOf(b.id) - usageOf(a.id)).map((tag) => {
                  const count = usageOf(tag.id);
                  const pct = Math.round((count / docs.length) * 100);
                  return (
                    <div className="tag-coverage__row" key={tag.id}>
                      <TagChip tag={tag.id} label={tag.name} tone={tag.tone} />
                      <span className="tag-coverage__bar"><span style={{ width: `${pct}%` }} /></span>
                      <span className="tag-coverage__count">{count} doc{count === 1 ? "" : "s"} · {pct}%</span>
                    </div>
                  );
                })}
                <div className="tag-coverage__row tag-coverage__row--untagged">
                  <span className="tag-coverage__label">Untagged</span>
                  <span className="tag-coverage__bar"><span style={{ width: `${100 - (coveragePct ?? 0)}%` }} /></span>
                  <span className="tag-coverage__count">{docs.length - taggedDocs} doc{docs.length - taggedDocs === 1 ? "" : "s"} · {100 - (coveragePct ?? 0)}%</span>
                </div>
              </div>
            ) : (
              <div className="tag-coverage tag-coverage--empty">Start the API server to analyze the live corpus.</div>
            )}
            <div className="curation-queue__foot">
              {coveragePct !== null && <span className="saved-note"><Ic.CheckCircle size={14} /> {coveragePct}% of documents carry at least one tag</span>}
              <Button onClick={() => setAnalyzing(false)}>Close</Button>
            </div>
          </div>
        )}

        {adding && (
          <div className="tag-composer">
            <Field label="Tag name"><Input autoFocus value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Compliance reviewed" /></Field>
            <Field label="Description"><Input value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="When should the team use it?" /></Field>
            <Field label="Color"><Select value={draft.tone} onChange={(event) => setDraft({ ...draft, tone: event.target.value })}><option value="blue">Dusty blue</option><option value="green">Moss</option><option value="gold">Ochre</option><option value="red">Brick</option><option value="ink">Ink</option></Select></Field>
            <Field label="Search weight"><Input type="number" step="0.05" min="0" value={draft.weight} onChange={(event) => setDraft({ ...draft, weight: event.target.value })} /></Field>
            <div className="tag-composer__actions"><Button onClick={() => { setAdding(false); setEditingId(null); }}>Cancel</Button><Button variant="primary" disabled={!draft.name.trim() || busy} onClick={saveTag}>{busy ? "Saving…" : editingId ? "Save tag" : "Create tag"}</Button></div>
          </div>
        )}

        <div className="tag-definition-list">
          {tagsQ.loading && (
            <div style={{ display: "grid", placeItems: "center", minHeight: 120 }}>
              <Spinner size="sm" label="Loading tags" />
            </div>
          )}
          {tagsQ.error && !tagsQ.data && (
            <EmptyState icon={<Ic.Tag size={20} />}>API offline — tag definitions unavailable.</EmptyState>
          )}
          {visible.map((tag) => (
            <article className="tag-definition" key={tag.id}>
              <div className="tag-definition__identity">
                <TagChip tag={tag.id} label={tag.name} tone={tag.tone} />
                <p>{tag.description}</p>
              </div>
              <div className="tag-definition__behaviors">{tag.behaviors.map((behavior) => <span key={behavior}>{behavior}</span>)}</div>
              <div className="tag-definition__evidence"><small>Evidence policy</small><span>{tag.evidence}</span></div>
              <div className="tag-definition__weight">
                <small>Search weight</small>
                {weightId === tag.id ? (
                  <Input
                    className="tag-weight-input"
                    type="number"
                    step="0.05"
                    min="0"
                    autoFocus
                    value={weightDraft}
                    onChange={(event) => setWeightDraft(event.target.value)}
                    onBlur={() => commitWeight(tag)}
                    onKeyDown={(event) => { if (event.key === "Enter") commitWeight(tag); if (event.key === "Escape") setWeightId(null); }}
                  />
                ) : (
                  <button className="tag-weight" title={`Adjust search weight for ${tag.name}`} onClick={() => { setWeightId(tag.id); setWeightDraft(String(tag.weight)); }}><strong>×{tag.weight.toFixed(2)}</strong></button>
                )}
              </div>
              <div className="tag-definition__usage"><strong>{usageOf(tag.id)}</strong><small>uses</small></div>
              <div className="tag-definition__tools">
                <Button icon title={`Edit ${tag.name}`} aria-label={`Edit ${tag.name}`} onClick={() => openComposer(tag)}><Ic.Pencil size={14} /></Button>
                {!tag.standard && (
                  <ConfirmButton
                    compact
                    confirmLabel="Delete?"
                    title={`Delete ${tag.name} — documents keep the raw tag but lose its behaviors`}
                    aria-label={`Delete ${tag.name}`}
                    onConfirm={() => removeTag(tag)}
                  >
                    <Ic.Trash size={14} />
                  </ConfirmButton>
                )}
              </div>
            </article>
          ))}
          {tagsQ.data && !visible.length && <EmptyState icon={<Ic.Tag size={20} />}>No tags match this search.</EmptyState>}
        </div>
      </Card>
    </div>
  );
}

type GuideLayer = { voice: string; terms: string; banned: string; inclusive: boolean; jargon: boolean; sentenceCase: boolean };
const GUIDE_LAYER_KEY = "mari.styleguide-layer";
const DEFAULT_LAYER: GuideLayer = {
  voice: "Calm, precise, and candid. Explain consequences before implementation detail. Never overstate certainty.",
  terms: "workspace → project\nmember → teammate\nsync → refresh",
  banned: "seamlessly, robust, leverage, game-changing",
  inclusive: true,
  jargon: true,
  sentenceCase: false,
};

function GuidesPanel() {
  const settings = useSettings();
  const wsName = settings.workspace?.name || "Workspace";
  const [active, setActive] = useState(() => localStorage.getItem("mari.styleguide") || "plain");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [custom, setCustom] = useState(false);
  const [layer, setLayer] = useState<GuideLayer>(() => {
    try { return { ...DEFAULT_LAYER, ...JSON.parse(localStorage.getItem(GUIDE_LAYER_KEY) || "{}") }; } catch { return DEFAULT_LAYER; }
  });

  const setDefault = (id: string) => {
    setActive(id);
    try { localStorage.setItem("mari.styleguide", id); } catch { /* stays session-local */ }
  };
  const saveLayer = () => {
    try { localStorage.setItem(GUIDE_LAYER_KEY, JSON.stringify(layer)); } catch { /* stays session-local */ }
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1800);
  };

  return (
    <div className="library-panel" role="tabpanel">
      <div className="library-two-col">
        <Card>
          <div className="sectionbar"><div><h3>Style guide packs</h3><p>Start from a trusted pack, then layer your project voice on top.</p></div><Button compact onClick={() => setCustom(true)}><Ic.Plus size={14} /> Custom guide</Button></div>
          {custom && <div className="inline-notice"><Ic.Pencil size={15} /><span><b>Untitled custom guide</b><small>Draft created from Plain language. Select it after naming and saving.</small></span><Button variant="link" onClick={() => setCustom(false)}>Dismiss</Button></div>}
          <div className="guide-list">
            {GUIDES.map((guide) => (
              <div className={`guide-card${active === guide.id ? " active" : ""}`} key={guide.id}>
                <span className={`guide-card__mark guide-card__mark--${guide.tone}`}><Ic.Quill size={19} /></span>
                <span className="guide-card__copy"><b>{guide.name}</b><small>{active === guide.id ? "Active · Project default" : "Built in"}</small><span>{guide.description}</span></span>
                <span className="guide-card__rules">{guide.rules}<small>rules</small></span>
                <span className="guide-card__check">{active === guide.id && <Ic.CheckCircle size={18} />}</span>
                <span className="guide-card__actions">
                  <Button variant="link" aria-expanded={expanded === guide.id} onClick={() => setExpanded((value) => (value === guide.id ? null : guide.id))}>{expanded === guide.id ? "Hide rules" : "View rules"}</Button>
                  {active === guide.id
                    ? <span className="guide-card__default"><Ic.Check size={12} /> Project default</span>
                    : <Button variant="link" onClick={() => setDefault(guide.id)}>Set as default</Button>}
                </span>
                {expanded === guide.id && (
                  <ul className="guide-card__rulelist">
                    {(GUIDE_RULES[guide.id] ?? []).map((rule) => <li key={rule}>{rule}</li>)}
                    <li className="guide-card__rulelist-more">…and {guide.rules - (GUIDE_RULES[guide.id]?.length ?? 0)} more in the rule registry</li>
                  </ul>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card className="guide-editor" eyebrow="Project layer" title={`${wsName} voice`}>
          <p className="guide-editor__intro">These choices override the active pack only for this project.</p>
          <Field label="Voice and tone"><Textarea value={layer.voice} onChange={(event) => setLayer({ ...layer, voice: event.target.value })} /></Field>
          <Field label="Preferred terms" hint="One mapping per line"><Textarea short value={layer.terms} onChange={(event) => setLayer({ ...layer, terms: event.target.value })} /></Field>
          <Field label="Zero-tolerance phrases"><Input value={layer.banned} onChange={(event) => setLayer({ ...layer, banned: event.target.value })} /></Field>
          <div className="guide-editor__checks">
            <label><input type="checkbox" checked={layer.inclusive} onChange={(event) => setLayer({ ...layer, inclusive: event.target.checked })} /> Enforce inclusive language</label>
            <label><input type="checkbox" checked={layer.jargon} onChange={(event) => setLayer({ ...layer, jargon: event.target.checked })} /> Flag unexplained jargon</label>
            <label><input type="checkbox" checked={layer.sentenceCase} onChange={(event) => setLayer({ ...layer, sentenceCase: event.target.checked })} /> Require sentence case headings</label>
          </div>
          <div className="guide-editor__foot">
            {saved && <span className="saved-note"><Ic.Check size={13} /> Saved</span>}
            <Button variant="primary" onClick={saveLayer}>Save project guide</Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

type TemplateItem = { id: string; name: string; category: string; description: string; sections: number; standard?: boolean; icon: typeof Ic.Doc };

const TEMPLATE_CATEGORIES = ["All", "Engineering", "Operations", "Product", "Team", "Governance"];

function TemplatesPanel({ custom, setCustom }: { custom: CustomTemplate[]; setCustom: (next: CustomTemplate[]) => void }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: "", category: "Engineering", description: "", sections: "5" });
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [usedId, setUsedId] = useState<string | null>(null);
  const [creatingId, setCreatingId] = useState<string | null>(null);

  const all: TemplateItem[] = [
    ...TEMPLATES,
    ...custom.map((template) => ({ ...template, standard: false, icon: Ic.Doc })),
  ];
  const visible = all.filter((template) => (category === "All" || template.category === category) && `${template.name} ${template.description}`.toLowerCase().includes(query.toLowerCase()));
  const sectionsFor = (template: TemplateItem) => TEMPLATE_SECTIONS[template.id] ?? GENERIC_SECTIONS.slice(0, Math.max(1, Math.min(template.sections, GENERIC_SECTIONS.length)));

  const addTemplate = () => {
    const name = draft.name.trim();
    if (!name) return;
    const id = `custom-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")}-${Date.now().toString(36)}`;
    setCustom([...custom, { id, name, category: draft.category, description: draft.description.trim() || "Custom team template.", sections: Math.max(1, Number(draft.sections) || 5) }]);
    setDraft({ name: "", category: "Engineering", description: "", sections: "5" });
    setAdding(false);
  };

  const removeTemplate = (template: TemplateItem) => {
    if (!window.confirm(`Delete the “${template.name}” template?`)) return;
    setCustom(custom.filter((item) => item.id !== template.id));
  };

  const draftFromTemplate = async (template: TemplateItem) => {
    if (creatingId) return;
    setCreatingId(template.id);
    await gql(
      `mutation($title: String!) { createTask(title: $title, kind: "approval", kindLabel: "Draft") }`,
      { title: `Draft from template: ${template.name}` },
    );
    setCreatingId(null);
    setUsedId(template.id);
    window.setTimeout(() => setUsedId((value) => (value === template.id ? null : value)), 2400);
  };

  return (
    <div className="library-panel" role="tabpanel">
      <Card>
        <div className="sectionbar">
          <div><h3>Document templates</h3><p>Shared scaffolds for the editor, chat, workflows, and the GitHub bot.</p></div>
          <div className="sectionbar__actions"><label className="compact-search"><Ic.Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a template" /></label><Button variant="primary" compact onClick={() => setAdding((value) => !value)}><Ic.Plus size={14} /> New template</Button></div>
        </div>

        {adding && (
          <div className="tag-composer template-composer">
            <Field label="Template name"><Input autoFocus value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Design review" /></Field>
            <Field label="Category"><Select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}>{TEMPLATE_CATEGORIES.slice(1).map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
            <Field label="Description"><Input value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="What does this scaffold cover?" /></Field>
            <Field label="Sections"><Input type="number" min="1" max="12" value={draft.sections} onChange={(event) => setDraft({ ...draft, sections: event.target.value })} /></Field>
            <div className="tag-composer__actions"><Button onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" disabled={!draft.name.trim()} onClick={addTemplate}>Create template</Button></div>
          </div>
        )}

        <div className="template-tabs">
          <Tabs
            variant="filter"
            ariaLabel="Template categories"
            value={category}
            onChange={setCategory}
            options={TEMPLATE_CATEGORIES.map((item) => ({ id: item, label: item }))}
          />
        </div>
        <div className="template-grid">
          {visible.map((template) => {
            const Icon = template.icon;
            return (
              <article className="template-card" key={template.id}>
                <div className="template-card__top">
                  <span className="template-card__icon"><Icon size={20} /></span>
                  <span className="template-card__category">{template.category}</span>
                  <span className="template-card__menu">
                    <Menu
                      trigger={
                        <Button icon compact aria-label={`More options for ${template.name}`}>
                          <Ic.Kebab size={15} />
                        </Button>
                      }
                    >
                      <MenuItem icon={<Ic.Eye size={14} />} onSelect={() => setPreviewId((value) => (value === template.id ? null : template.id))}>
                        {previewId === template.id ? "Hide sections" : "Preview sections"}
                      </MenuItem>
                      <MenuItem icon={<Ic.Quill size={14} />} onSelect={() => draftFromTemplate(template)}>Use template</MenuItem>
                      {!template.standard && (
                        <MenuItem danger icon={<Ic.Trash size={14} />} onSelect={() => removeTemplate(template)}>Delete template</MenuItem>
                      )}
                    </Menu>
                  </span>
                </div>
                <h4>{template.name}</h4><p>{template.description}</p>
                {previewId === template.id && (
                  <ol className="template-card__sections">
                    {sectionsFor(template).map((section) => <li key={section}>{section}</li>)}
                  </ol>
                )}
                <div className="template-card__meta"><span>{template.sections} required sections</span>{template.standard ? <span>Standard</span> : <span>Custom</span>}</div>
                <div className="template-card__actions">
                  {usedId === template.id
                    ? <span className="saved-note"><Ic.CheckCircle size={14} /> Draft task created</span>
                    : <Button compact disabled={creatingId === template.id} onClick={() => draftFromTemplate(template)}>{creatingId === template.id ? "Creating…" : "Use template"}</Button>}
                  <Button variant="link" aria-expanded={previewId === template.id} onClick={() => setPreviewId((value) => (value === template.id ? null : template.id))}>{previewId === template.id ? "Hide" : "Preview"}</Button>
                </div>
              </article>
            );
          })}
          {!visible.length && (
            <div className="template-grid__empty">
              <EmptyState icon={<Ic.Doc size={20} />}>No templates match these filters.</EmptyState>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

export default function LibraryPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: LibraryTab = raw === "rules" || raw === "guides" || raw === "glossary" || raw === "templates" ? raw : "tags";
  const setTab = (next: LibraryTab) => setParams({ tab: next }, { replace: true });

  const tagsQ = useQuery<TagDefinition[]>(TAGDEFS_QUERY, { map: mapTagDefs });
  const glossaryQ = useQuery<GlossaryTerm[]>(GLOSSARY_QUERY, { map: mapGlossary });
  const [customTemplates, setCustomTemplates] = useState<CustomTemplate[]>(loadCustomTemplates);
  const saveCustomTemplates = (next: CustomTemplate[]) => {
    setCustomTemplates(next);
    try { localStorage.setItem(TEMPLATES_KEY, JSON.stringify(next)); } catch { /* stays session-local */ }
  };

  return (
    <>
      <PageHeader eyebrow="Editorial system" title="Library" description="Project-wide vocabulary, deterministic rules, voice, and scaffolds for every document." actions={<Button onClick={() => navigate("/welcome")}><Ic.Sprout size={14} /> Setup guide</Button>} />
      <Tabs value={tab} onChange={setTab} ariaLabel="Library sections" options={[{ id: "tags", label: "Tags", count: (tagsQ.data ?? []).length }, { id: "rules", label: "Rules", count: RULE_CATALOG_COUNT }, { id: "guides", label: "Style guides", count: GUIDES.length }, { id: "glossary", label: "Glossary", count: (glossaryQ.data ?? []).length }, { id: "templates", label: "Templates", count: TEMPLATES.length + customTemplates.length }]} />
      {tab === "tags" ? <TagsPanel tagsQ={tagsQ} /> : tab === "rules" ? <RulesPanel /> : tab === "guides" ? <GuidesPanel /> : tab === "glossary" ? <GlossaryPanel glossaryQ={glossaryQ} /> : <TemplatesPanel custom={customTemplates} setCustom={saveCustomTemplates} />}
    </>
  );
}

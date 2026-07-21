import { useEffect, useMemo, useRef, useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Card, Chip, ChipTone, EmptyState, Field, Select, Tabs, Textarea } from "../../components/ui";
import { saveSetting, useSave, useSettings } from "../settings/shared";

type Family = "AI slop" | "Clarity" | "Style guide" | "Inclusive";
type Severity = "error" | "warn" | "advisory";
type RuleStatus = "active" | "zero" | "ignored";
type Rule = { id: string; family: Family; severity: Severity; pack: string; description: string };
type DetectorConfig = {
  styleGuide: string;
  grammar: boolean;
  ignoreRules: string[];
  zeroTolerance: string[];
};
type Finding = { id: string; family: Family; severity: Severity; start: number; end: number; suggestion: string };

export const RULE_CATALOG_COUNT = 170;

const SEVERITY_TONE: Record<Severity, ChipTone> = { error: "red", warn: "gold", advisory: "ink" };

const RULES: Rule[] = [
  { id: "overused-word", family: "AI slop", severity: "warn", pack: "Core", description: "Flags high-frequency stock terms such as robust, seamless, and leverage." },
  { id: "marketing-buzzword", family: "AI slop", severity: "warn", pack: "Core", description: "Catches promotional phrases that obscure the actual claim." },
  { id: "manufactured-contrast", family: "AI slop", severity: "advisory", pack: "Core", description: "Finds reflexive “not only … but” contrast patterns." },
  { id: "em-dash-overuse", family: "AI slop", severity: "advisory", pack: "Core", description: "Flags dense em-dash use when a sentence break would read better." },
  { id: "passive-voice", family: "Clarity", severity: "warn", pack: "Core", description: "Surfaces passive constructions so the actor can be made explicit." },
  { id: "long-sentence", family: "Clarity", severity: "warn", pack: "Core", description: "Flags sentences that exceed the configured readability threshold." },
  { id: "nominalization", family: "Clarity", severity: "advisory", pack: "Core", description: "Suggests direct verbs in place of abstract noun phrases." },
  { id: "there-is-expletive", family: "Clarity", severity: "advisory", pack: "Core", description: "Finds sentences opened with empty “there is” constructions." },
  { id: "undefined-acronym", family: "Clarity", severity: "warn", pack: "Core", description: "Flags acronyms that are not expanded on first use." },
  { id: "sentence-case-heading", family: "Style guide", severity: "warn", pack: "All guides", description: "Keeps headings in sentence case for a consistent document hierarchy." },
  { id: "word-swap", family: "Style guide", severity: "warn", pack: "Guide list", description: "Applies the active guide’s preferred-word mappings." },
  { id: "use-contractions", family: "Style guide", severity: "advisory", pack: "Microsoft", description: "Suggests natural contractions when the active guide prefers them." },
  { id: "no-please-instructions", family: "Style guide", severity: "advisory", pack: "Microsoft", description: "Removes “please” from direct task instructions." },
  { id: "terminology-consistency", family: "Style guide", severity: "warn", pack: "All guides", description: "Finds competing forms of the same product term." },
  { id: "vague-link-text", family: "Inclusive", severity: "error", pack: "Accessible", description: "Flags links such as “click here” and “learn more” that lack context." },
  { id: "gendered-language", family: "Inclusive", severity: "error", pack: "Inclusive", description: "Suggests clear, inclusive alternatives for gendered role nouns." },
  { id: "missing-alt-text", family: "Inclusive", severity: "error", pack: "Accessible", description: "Finds Markdown images without alternative text." },
];

const DEFAULT_CONFIG: DetectorConfig = {
  styleGuide: "plain",
  grammar: true,
  ignoreRules: ["use-contractions"],
  zeroTolerance: ["vague-link-text", "gendered-language"],
};

/** Rule toggles also persist locally so unsaved edits survive a reload. */
const RULES_LOCAL_KEY = "mari.rules";
function loadLocalConfig(): DetectorConfig | null {
  try {
    const raw = localStorage.getItem(RULES_LOCAL_KEY);
    return raw ? { ...DEFAULT_CONFIG, ...JSON.parse(raw) } : null;
  } catch { return null; }
}
function storeLocalConfig(config: DetectorConfig) {
  try { localStorage.setItem(RULES_LOCAL_KEY, JSON.stringify(config)); } catch { /* stays session-local */ }
}

const DOCS = [
  {
    id: "rollout",
    title: "Authentication rollout plan",
    source: "Google Docs · canonical",
    text: "# Authentication Rollout Plan\n\nThis game-changing update will leverage a robust authentication layer across every service. There is a configuration that was enabled by the team before the SSO migration began.\n\nPlease [click here](https://example.com) to learn more. The rollout was completed after product, security, and support reviewed the change, documented the fallback, tested the migration, and agreed on the final launch window.",
  },
  {
    id: "api",
    title: "Partner API migration",
    source: "GitHub · draft",
    text: "# Partner API Migration\n\nWe will utilize the new endpoint to execute the migration seamlessly. There are several changes that were approved. The login flow and sign in flow should remain consistent.\n\n![](migration.png)",
  },
  {
    id: "runbook",
    title: "Incident response runbook",
    source: "Notion · needs review",
    text: "# Incident Response Runbook\n\nGuys, use the dashboard to inspect the service. The mitigation was deployed by the incident commander—then validated by support—before traffic was restored. [Learn more](https://example.com).",
  },
];

const FAMILY_META: Record<Family, { key: string; help: string }> = {
  "AI slop": { key: "slop", help: "Stock language and repeated rhetorical patterns" },
  Clarity: { key: "clarity", help: "Readability, directness, and sentence structure" },
  "Style guide": { key: "style", help: "The active project style pack" },
  Inclusive: { key: "inclusive", help: "Inclusive and accessible language" },
};

function findMatches(text: string, re: RegExp, rule: Rule, suggestion: string): Finding[] {
  const matches: Finding[] = [];
  for (const match of text.matchAll(re)) {
    if (match.index === undefined) continue;
    matches.push({ id: rule.id, family: rule.family, severity: rule.severity, start: match.index, end: match.index + match[0].length, suggestion });
  }
  return matches;
}

function runDetector(text: string, config: DetectorConfig): Finding[] {
  const byId = Object.fromEntries(RULES.map((rule) => [rule.id, rule])) as Record<string, Rule>;
  const findings: Finding[] = [];
  const add = (id: string, re: RegExp, suggestion: string) => {
    if (!config.ignoreRules.includes(id)) findings.push(...findMatches(text, re, byId[id], suggestion));
  };

  add("overused-word", /\b(?:robust|seamless(?:ly)?|leverage)\b/gi, "Name the concrete capability or outcome instead.");
  add("marketing-buzzword", /\b(?:game-changing|world-class|cutting-edge|best-in-class)\b/gi, "Replace the claim with specific evidence.");
  add("passive-voice", /\b(?:is|are|was|were|be|been)\s+\w+ed\b/gi, "Name the actor and use an active verb.");
  add("there-is-expletive", /\bthere (?:is|are|was|were)\b/gi, "Start with the subject of the sentence.");
  add("undefined-acronym", /\b(?:SSO|SLA|MFA)\b/g, "Expand this acronym on first use.");
  add("word-swap", /\b(?:utilize|execute)\b/gi, "Prefer “use” or “run.”");
  add("no-please-instructions", /\bplease\b/gi, "State the instruction directly.");
  add("vague-link-text", /\b(?:click here|learn more)\b/gi, "Describe the link destination or action.");
  add("gendered-language", /\b(?:guys|chairman|manpower)\b/gi, "Use a specific, inclusive audience or role name.");
  add("missing-alt-text", /!\[\]\([^)]+\)/g, "Add concise alternative text for this image.");
  add("em-dash-overuse", /—[^\n—]+—/g, "Consider two sentences or parentheses.");

  if (!config.ignoreRules.includes("sentence-case-heading")) {
    for (const match of text.matchAll(/^#{1,6}\s+(.+)$/gm)) {
      const heading = match[1];
      const words = heading.split(/\s+/).slice(1);
      if (words.some((word) => /^[A-Z][a-z]/.test(word))) {
        const start = (match.index ?? 0) + match[0].indexOf(heading);
        findings.push({ id: "sentence-case-heading", family: "Style guide", severity: "warn", start, end: start + heading.length, suggestion: `Use sentence case: “${heading.charAt(0)}${heading.slice(1).toLowerCase()}”.` });
      }
    }
  }

  if (!config.ignoreRules.includes("long-sentence")) {
    for (const match of text.matchAll(/[^\n.!?]+[.!?]/g)) {
      if (match[0].trim().split(/\s+/).length > 24) {
        const start = match.index ?? 0;
        findings.push({ id: "long-sentence", family: "Clarity", severity: "warn", start, end: start + match[0].length, suggestion: "Split this sentence at its main change of thought." });
      }
    }
  }

  if (!config.ignoreRules.includes("terminology-consistency") && /\blogin\b/i.test(text) && /\bsign in\b/i.test(text)) {
    const match = /\blogin\b/i.exec(text)!;
    findings.push({ id: "terminology-consistency", family: "Style guide", severity: "warn", start: match.index, end: match.index + match[0].length, suggestion: "Use “sign in” consistently for the verb." });
  }

  return findings.sort((a, b) => a.start - b.start);
}

function FindingExcerpt({ finding, text }: { finding: Finding; text: string }) {
  const from = Math.max(0, finding.start - 34);
  const to = Math.min(text.length, finding.end + 46);
  return (
    <span className="rule-finding__excerpt">
      {from > 0 && "…"}{text.slice(from, finding.start)}<mark>{text.slice(finding.start, finding.end)}</mark>{text.slice(finding.end, to)}{to < text.length && "…"}
    </span>
  );
}

type RuleView = "all" | "zero" | "ignored";

export default function RulesPanel() {
  const settings = useSettings();
  const saver = useSave();
  const [config, setConfigState] = useState<DetectorConfig>(() => loadLocalConfig() ?? DEFAULT_CONFIG);
  const hadLocal = useRef(loadLocalConfig() !== null);
  const [dirty, setDirty] = useState(false);
  const setConfig = (next: DetectorConfig | ((current: DetectorConfig) => DetectorConfig)) => {
    setConfigState((current) => {
      const value = typeof next === "function" ? next(current) : next;
      storeLocalConfig(value);
      return value;
    });
  };
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState<Family | "All">("All");
  const [view, setView] = useState<RuleView>("all");
  const [docId, setDocId] = useState(DOCS[0].id);
  const [text, setText] = useState(DOCS[0].text);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    // Hydrate from the project settings unless local (unsaved) edits exist.
    if (!dirty && !hadLocal.current && settings.detector) setConfig({ ...DEFAULT_CONFIG, ...settings.detector });
  }, [settings.detector, dirty]);

  const statusOf = (id: string): RuleStatus => config.ignoreRules.includes(id) ? "ignored" : config.zeroTolerance.includes(id) ? "zero" : "active";
  const setStatus = (id: string, status: RuleStatus) => {
    setDirty(true);
    setConfig((current) => ({
      ...current,
      ignoreRules: status === "ignored" ? [...new Set([...current.ignoreRules, id])] : current.ignoreRules.filter((item) => item !== id),
      zeroTolerance: status === "zero" ? [...new Set([...current.zeroTolerance, id])] : current.zeroTolerance.filter((item) => item !== id),
    }));
  };

  const visible = useMemo(() => RULES.filter((rule) => {
    const status: RuleStatus = config.ignoreRules.includes(rule.id) ? "ignored" : config.zeroTolerance.includes(rule.id) ? "zero" : "active";
    return (family === "All" || rule.family === family)
      && (view === "all" || view === status)
      && `${rule.id} ${rule.description} ${rule.family} ${rule.pack}`.toLowerCase().includes(query.toLowerCase());
  }), [config, family, query, view]);

  const check = () => { setFindings(runDetector(text, config)); setChecked(true); };
  const errors = findings.filter((item) => item.severity === "error").length;
  const warnings = findings.filter((item) => item.severity === "warn").length;
  const score = Math.min(100, findings.reduce((total, item) => total + (item.severity === "error" ? 9 : item.severity === "warn" ? 6 : 3), 0));
  const band = score < 12 ? "clean" : score < 30 ? "light" : score < 60 ? "moderate" : "heavy";

  return (
    <div className="library-panel rules-panel" role="tabpanel">
      <div className="project-config-strip">
        <span className="project-config-strip__mark"><Ic.Layers size={18} /></span>
        <span><b>{settings.workspace?.name || "Workspace"}</b><small>Rules run in the deterministic prose engine · unsaved toggles are kept in this browser until you save to the project</small></span>
        <label>Style pack<Select value={config.styleGuide} onChange={(event) => { setDirty(true); setConfig({ ...config, styleGuide: event.target.value }); }}><option value="plain">Plain language</option><option value="microsoft">Microsoft</option><option value="google">Google developer</option><option value="ap">AP style</option><option value="chicago">Chicago</option></Select></label>
        <label className="project-config-strip__check"><input type="checkbox" checked={config.grammar} onChange={(event) => { setDirty(true); setConfig({ ...config, grammar: event.target.checked }); }} /> Grammar</label>
        {saver.saved && <span className="saved-note"><Ic.Check size={13} /> Saved to project</span>}
        <Button variant="primary" compact disabled={saver.saving || !dirty} onClick={() => saver.run(async () => { await saveSetting("detector", config); setDirty(false); })}>{saver.saving ? "Saving…" : "Save project rules"}</Button>
      </div>

      <div className="rule-family-grid" aria-label="Rule families">
        {Object.entries(FAMILY_META).map(([name, meta]) => {
          const count = RULES.filter((rule) => rule.family === name).length;
          return <button key={name} className={`rule-family rule-family--${meta.key}${family === name ? " active" : ""}`} onClick={() => setFamily(family === name ? "All" : name as Family)}><span>{count}</span><b>{name}</b><small>{meta.help}</small></button>;
        })}
      </div>

      <div className="rules-workspace">
        <Card className="rule-registry">
          <div className="sectionbar">
            <div><h3>Deterministic rule registry</h3><p>{RULE_CATALOG_COUNT}+ rules · 49 configurable lists · every finding has an ID and span</p></div>
            <label className="compact-search"><Ic.Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a rule or family" /></label>
          </div>
          <div className="rule-view-filter">
            <Tabs
              variant="filter"
              ariaLabel="Rule status"
              value={view}
              onChange={setView}
              options={[
                { id: "all", label: "All rules", count: RULE_CATALOG_COUNT },
                { id: "zero", label: "Zero tolerance", count: config.zeroTolerance.length },
                { id: "ignored", label: "Ignored", count: config.ignoreRules.length },
              ]}
            />
          </div>
          <div className="rule-table-head"><span>Rule</span><span>Family / pack</span><span>Severity</span><span>Project behavior</span></div>
          <div className="rule-list">
            {visible.map((rule) => {
              const status = statusOf(rule.id);
              return (
                <article className="rule-row" key={rule.id}>
                  <span className="rule-row__identity"><code>{rule.id}</code><small>{rule.description}</small></span>
                  <span className="rule-row__family"><b>{rule.family}</b><small>{rule.pack}</small></span>
                  <Chip tone={SEVERITY_TONE[rule.severity]} caps>{rule.severity}</Chip>
                  <span className="rule-status-actions" aria-label={`${rule.id} project behavior`}>
                    {([['active', 'Active'], ['zero', 'Zero'], ['ignored', 'Ignore']] as const).map(([id, label]) => <button key={id} className={status === id ? `active active--${id}` : ""} aria-pressed={status === id} onClick={() => setStatus(rule.id, id)}>{label}</button>)}
                  </span>
                </article>
              );
            })}
            {!visible.length && <EmptyState icon={<Ic.Search size={20} />}>No rules match these filters.</EmptyState>}
          </div>
        </Card>

        <Card className="document-checker">
          <div className="sectionbar"><div><span className="eyebrow">Document check</span><h3>Flag against project rules</h3><p>Deterministic only. No authorship guesses.</p></div><span className="document-checker__shield"><Ic.ShieldCheck size={21} /></span></div>
          <Field label="Document"><Select value={docId} onChange={(event) => { const doc = DOCS.find((item) => item.id === event.target.value)!; setDocId(doc.id); setText(doc.text); setChecked(false); setFindings([]); }}>
            {DOCS.map((doc) => <option value={doc.id} key={doc.id}>{doc.title}</option>)}
          </Select></Field>
          <span className="document-checker__source">{DOCS.find((doc) => doc.id === docId)?.source}</span>
          <Field label="Content"><Textarea className="rule-document-text" value={text} onChange={(event) => { setText(event.target.value); setChecked(false); }} /></Field>
          <Button variant="primary" className="document-checker__run" onClick={check}><Ic.Play size={14} /> Run deterministic check</Button>

          {checked && (
            <div className="check-results" aria-live="polite">
              <div className="check-score">
                <span className={`check-score__number check-score__number--${band}`}>{score}</span>
                <span><b>{band} slop score</b><small>{findings.length} findings · {errors} errors · {warnings} warnings</small></span>
              </div>
              <div className="rule-findings">
                {findings.slice(0, 8).map((finding, index) => (
                  <article className="rule-finding" key={`${finding.id}-${finding.start}-${index}`}>
                    <div>
                      <code>{finding.id}</code>
                      <Chip tone={SEVERITY_TONE[finding.severity]} caps>{finding.severity}</Chip>
                      {config.zeroTolerance.includes(finding.id) && <Chip tone="red" caps className="rule-finding__zero">zero tolerance</Chip>}
                    </div>
                    <FindingExcerpt finding={finding} text={text} />
                    <small>{finding.suggestion}</small>
                    <Button variant="link" className="rule-finding__ignore" onClick={() => { setStatus(finding.id, "ignored"); setFindings((items) => items.filter((item) => item.id !== finding.id)); }}>Ignore this rule project-wide</Button>
                  </article>
                ))}
                {!findings.length && (
                  <EmptyState icon={<Ic.CheckCircle size={24} />} title="No configured rules fired">
                    This content passes the current project configuration.
                  </EmptyState>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

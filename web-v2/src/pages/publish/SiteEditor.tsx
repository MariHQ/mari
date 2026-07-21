// Publish — per-site editor: Content / Navigation / Theme / Deploy config
// tabs on the left, a live iframe preview of the REAL built site on the right,
// AI theme customization (config-only), and S3 deploys with honest notes.

import { useEffect, useMemo, useRef, useState } from "react";
import * as Ic from "../../components/icons";
import { TagChip } from "../../components/TagChip";
import {
  Button, Card, Chip, Field, Input, PageHeader, SectionLabel, Select, StatusChip, Stepper, Tabs, useToast,
} from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import {
  ACCENTS, GENERATORS, Release, Site, THEMES, buildSiteEx,
  cap, fmtDeployed, normalizeGates, radiusToLetter, radiusToNumber, relDot, releasesQ, sitePath, useBuilt,
} from "./data";

type EditorTab = "content" | "navigation" | "theme" | "deploy";

export function SiteEditor({ site }: { site: Site }) {
  const t0: any = site.theme ?? {};
  const [tab, setTab] = useState<EditorTab>("content");
  const [theme, setTheme] = useState(() => THEMES.find((x) => x.name === t0.theme) ?? THEMES[0]);
  const [accent, setAccent] = useState<string>(t0.accent ?? THEMES[0].accent);
  const [radius, setRadius] = useState(t0.radius != null ? radiusToLetter(Number(t0.radius)) : "M");
  const [density, setDensity] = useState(t0.density ? cap(String(t0.density)) : "Comfortable");
  const [mode, setMode] = useState<"Light" | "Dark">(String(t0.mode).toLowerCase() === "dark" ? "Dark" : "Light");

  const [dirty, setDirty] = useState(false);
  const [buildNonce, setBuildNonce] = useState(0);
  const [building, setBuilding] = useState(false);
  const built = useBuilt(site.id, buildNonce);
  const toast = useToast();

  // generator picker — preselected from the site's persisted theme.generator
  const [generator, setGenerator] = useState<string>(
    t0.generator === "docusaurus" ? "docusaurus" : "mari",
  );
  const [buildErr, setBuildErr] = useState<string | null>(null);
  const [buildInfo, setBuildInfo] = useState<{ msg: string; url?: string } | null>(null);

  const releasesQuery = useQuery<Release[]>(releasesQ(site.id), { map: (d: any) => d.releases ?? [] });
  const releases = releasesQuery.data ?? [];
  const live = releases.find((r) => r.status === "live") ?? releases[0] ?? null;
  const [deploying, setDeploying] = useState(false);
  const [deployNote, setDeployNote] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState<number | null>(null);

  // AI customize
  const [instr, setInstr] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiNote, setAiNote] = useState<string | null>(null);

  // deploy target config (settings key 'deploy')
  const settingsQ = useQuery<{ key: string; value: any }[]>(`{ settings { key value } }`, { map: (d: any) => d.settings ?? [] });
  const settings = useMemo(() => settingsQ.data ?? [], [settingsQ.data]);
  const [bucket, setBucket] = useState("");
  const [region, setRegion] = useState("");
  const [cname, setCname] = useState("");
  const cfgSeeded = useRef(false);
  useEffect(() => {
    const v = settings.find((s) => s.key === "deploy")?.value;
    if (v && !cfgSeeded.current) {
      cfgSeeded.current = true;
      setBucket(v.bucket ?? ""); setRegion(v.region ?? ""); setCname(v.cname ?? "");
    }
  }, [settings]);
  const [savingCfg, setSavingCfg] = useState(false);
  const [savedCfg, setSavedCfg] = useState(false);
  const saveCfg = async () => {
    if (savingCfg) return;
    setSavingCfg(true);
    await gql(`mutation($v: JSON!) { updateSetting(key: "deploy", value: $v) }`, { v: { bucket, region, cname } });
    setSavingCfg(false); setSavedCfg(true);
    setTimeout(() => setSavedCfg(false), 1600);
  };

  const persistTheme = (patch: Record<string, unknown>) => {
    setDirty(true);
    void gql(`mutation($t: JSON!) { updateSiteTheme(id: ${site.id}, theme: $t) }`, { t: patch });
  };
  const pickTheme = (t: (typeof THEMES)[number]) => { setTheme(t); setAccent(t.accent); persistTheme({ theme: t.name, accent: t.accent }); };
  const pickAccent = (c: string) => { setAccent(c); persistTheme({ accent: c }); };
  const pickRadius = (r: string) => { setRadius(r); persistTheme({ radius: radiusToNumber(r) }); };
  const pickDensity = (d: string) => { setDensity(d); persistTheme({ density: d.toLowerCase() }); };
  const pickMode = (m: "Light" | "Dark") => { setMode(m); persistTheme({ mode: m.toLowerCase() }); };

  const applyPatch = (p: any) => {
    if (p.theme) { const f = THEMES.find((x) => x.name === p.theme); if (f) setTheme(f); }
    if (p.accent) setAccent(String(p.accent));
    if (p.radius != null) setRadius(radiusToLetter(Number(p.radius)));
    if (p.density) setDensity(cap(String(p.density)));
    if (p.mode) setMode(String(p.mode).toLowerCase() === "dark" ? "Dark" : "Light");
  };

  const askMari = async () => {
    if (!instr.trim() || aiBusy) return;
    setAiBusy(true); setAiNote(null);
    const d = await gql<{ aiCustomizeSite: Record<string, unknown> }>(
      `mutation($i: String!) { aiCustomizeSite(id: ${site.id}, instruction: $i) }`, { i: instr.trim() }
    );
    const patch = d?.aiCustomizeSite ?? {};
    const keys = Object.keys(patch);
    if (keys.length) {
      applyPatch(patch);
      setDirty(true);
      setAiNote(`Mari changed: ${keys.map((k) => `${k} → ${String((patch as any)[k])}`).join(", ")}`);
    } else {
      setAiNote(d ? "Mari made no changes (model unavailable or nothing to change)." : "Couldn't reach Mari — is the API running?");
    }
    setAiBusy(false);
  };

  const rebuild = async () => {
    if (building) return;
    setBuilding(true); setBuildErr(null); setBuildInfo(null);
    const res = await buildSiteEx(site.id, generator);
    setBuilding(false);
    if (!res) {
      setBuildErr("Couldn't reach the API — nothing was built.");
      return;
    }
    if (!res.ok) {
      setBuildErr(`${GENERATORS.find((g) => g.key === res.generator)?.label ?? res.generator} build failed: ${res.error ?? "unknown error"}`);
      return;
    }
    setDirty(false);
    setBuildNonce((n) => n + 1);
    const genLabel = GENERATORS.find((g) => g.key === res.generator)?.label ?? res.generator;
    setBuildInfo({ msg: `Built with ${genLabel} — ${res.pages ?? "?"} pages in ${res.seconds ?? "?"}s.`, url: res.url });
    toast(`Site built — ${res.pages ?? "?"} pages in ${res.seconds ?? "?"}s (${genLabel}).`, "success");
  };

  const deploy = async () => {
    if (deploying) return;
    setDeploying(true); setDeployNote(null);
    const d = await gql<{ deploySite: string }>(`mutation { deploySite(id: ${site.id}) }`);
    setDeploying(false);
    setDeployNote(d?.deploySite ? `Deployed ${d.deploySite}` : "Deploy failed — is the API running?");
    if (d?.deploySite) { setDirty(false); setBuildNonce((n) => n + 1); }
    releasesQuery.refetch();
  };

  const rollback = async (id: number) => {
    setRollingBack(id);
    await gql(`mutation { rollbackRelease(id: ${id}) }`);
    setRollingBack(null);
    releasesQuery.refetch();
  };

  const navTree: any[] = Array.isArray(site.nav) ? site.nav : [];
  const gates = normalizeGates(site.gates) ?? [];
  const sources = site.sources ?? ["customer-facing", "canonical"];
  // Suggested bucket derives from this site's own domain (S3 buckets are
  // dns-like), so the field proposes a real name instead of a stock example.
  const suggestedBucket = (site.domain || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const history = releases.filter((r) => r !== live);
  // Light deploy-pipeline treatment: Build → Upload → Release.
  const deployStep = deploying ? 1 : deployNote?.startsWith("Deployed") ? 3 : 0;

  return (
    <>
      <PageHeader
        title={site.name}
        backLink={{ to: "/publish", label: "All sites" }}
        actions={(
          <>
            {site.status === "live" ? <StatusChip status="running" label="Live" /> : <StatusChip status="draft" />}
            <span className="mono">{site.domain}</span>
            {built && (
              <a className="btn" href={sitePath(site.id)} target="_blank" rel="noreferrer">
                Open site <Ic.External size={13} />
              </a>
            )}
            <Button variant="primary" disabled={deploying} onClick={deploy}>
              <Ic.Send size={14} /> {deploying ? "Deploying…" : "Deploy"}
            </Button>
          </>
        )}
      />

      <div className="pub-editor">
        {/* left: config card — Content / Navigation / Theme / Deploy */}
        <Card className="pub-config">
          <Tabs<EditorTab>
            value={tab}
            onChange={setTab}
            ariaLabel="Site configuration"
            variant="underline"
            options={[
              { id: "content", label: "Content" },
              { id: "navigation", label: "Nav" },
              { id: "theme", label: "Theme" },
              { id: "deploy", label: "Deploy" },
            ]}
          />

          {tab === "content" && (
            <>
              <SectionLabel className="pub-sec">Sources</SectionLabel>
              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                {sources.map((s) => <TagChip key={s} tag={s} />)}
              </div>
              <div className="card__hint" style={{ marginTop: 6 }}>
                Sources are set at creation — the builder pulls every doc carrying these tags.
              </div>

              <SectionLabel className="pub-sec">Documents</SectionLabel>
              <div className="row pub-docs">
                <Ic.Doc size={14} /> {site.docs} docs match
                {site.warnings > 0 && (
                  <>
                    <span className="card__spacer" />
                    <Chip tone="gold" dot>{site.warnings} warnings</Chip>
                  </>
                )}
              </div>

              <div className="pub-divider">
                <SectionLabel>Pre-publish gates</SectionLabel>
                {gates.length ? gates.map((g) => (
                  <div key={g.name} className="pub-gate">
                    <span className="row" style={{ gap: 8 }}>
                      <Ic.CheckCircle size={14} style={{ color: g.ok ? "var(--green-deep)" : "var(--gold)" } as any} /> {g.name}
                    </span>
                    <span className={`pub-gate__note${g.ok ? "" : " pub-gate__note--warn"}`}>{g.note}</span>
                  </div>
                )) : <div className="card__hint" style={{ marginTop: 6 }}>No gates configured for this site yet.</div>}
              </div>
            </>
          )}

          {tab === "navigation" && (
            <>
              <SectionLabel className="pub-sec">Navigation</SectionLabel>
              {navTree.length ? navTree.map((n: any) => (
                <div key={n.label}>
                  <div className={`pub-nav__item${n.active ? " pub-nav__item--active" : ""}`}>
                    <Ic.Doc size={13} /> {n.label}
                  </div>
                  {(n.children ?? []).map((c: string) => (
                    <div key={c} className="pub-nav__child">
                      <Ic.Doc size={12} /> {c}
                    </div>
                  ))}
                </div>
              )) : (
                <div className="card__hint">No explicit nav — the builder derives sections from doc headings at build time.</div>
              )}
              <div className="card__hint" style={{ marginTop: 8 }}>
                The built site orders pages by this outline; sections without docs are skipped.
              </div>
            </>
          )}

          {tab === "theme" && (
            <>
              <div className="row pub-sec" style={{ justifyContent: "space-between" }}>
                <SectionLabel>Theme</SectionLabel>
                <span className="card__hint">Saved as you pick <Ic.Sparkle size={11} /></span>
              </div>

              <div className="pub-swatches">
                {THEMES.map((t) => (
                  <button key={t.key} type="button" className={`pub-swatch${theme.key === t.key ? " selected" : ""}`} onClick={() => pickTheme(t)}>
                    <span
                      className="pub-swatch__frame"
                      style={{ background: t.bg, borderColor: theme.key === t.key ? t.accent : undefined }}
                    >
                      <span className="pub-swatch__bar" style={{ background: t.accent }} />
                      <span className="pub-swatch__line" />
                      <span className="pub-swatch__line pub-swatch__line--short" />
                      {theme.key === t.key && (
                        <span className="pub-swatch__check" style={{ background: t.accent }}><Ic.Check size={9} /></span>
                      )}
                    </span>
                    <span className="pub-swatch__name">{t.name}</span>
                  </button>
                ))}
              </div>

              <div className="row" style={{ marginTop: 12 }}>
                <span className="pub-optlabel">Accent</span>
                <span className="row" style={{ gap: 6 }}>
                  {ACCENTS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-label={`Accent ${c}`}
                      className={`pub-accent${accent === c ? " selected" : ""}`}
                      style={{ background: c }}
                      onClick={() => pickAccent(c)}
                    />
                  ))}
                  <label className={`pub-accent-custom${ACCENTS.includes(accent) ? "" : " selected"}`}>
                    Custom
                    <input type="color" value={accent} onChange={(e) => pickAccent(e.target.value)} />
                  </label>
                </span>
              </div>

              <div className="row" style={{ marginTop: 10 }}>
                <span className="pub-optlabel">Radius</span>
                <span className="row" style={{ gap: 6 }}>
                  {["S", "M", "L"].map((r) => (
                    <Button key={r} compact className={radius === r ? "pub-on" : undefined} aria-pressed={radius === r} onClick={() => pickRadius(r)}>{r}</Button>
                  ))}
                </span>
              </div>

              <div className="row" style={{ marginTop: 10, alignItems: "flex-start" }}>
                <span className="pub-optlabel">Density</span>
                <span className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                  {["Comfortable", "Compact", "Dense"].map((d) => (
                    <Button key={d} compact className={`pub-opt-sm${density === d ? " pub-on" : ""}`} aria-pressed={density === d} onClick={() => pickDensity(d)}>{d}</Button>
                  ))}
                </span>
              </div>

              <div className="row" style={{ marginTop: 10 }}>
                <span className="pub-optlabel">Mode</span>
                <span className="row" style={{ gap: 6 }}>
                  {(["Light", "Dark"] as const).map((m) => (
                    <Button key={m} compact className={mode === m ? "pub-on" : undefined} aria-pressed={mode === m} onClick={() => pickMode(m)}>{m}</Button>
                  ))}
                </span>
              </div>

              <div className="pub-divider">
                <SectionLabel><Ic.Sparkle size={11} /> AI customize</SectionLabel>
                <div className="row" style={{ gap: 6, marginTop: 8 }}>
                  <Input
                    value={instr}
                    placeholder="Describe it — e.g. 'dark, blue accent, denser'"
                    onChange={(e) => setInstr(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && askMari()}
                  />
                  <Button compact style={{ flex: "none" }} disabled={aiBusy || !instr.trim()} onClick={askMari}>
                    {aiBusy ? "…" : "Ask Mari"}
                  </Button>
                </div>
                <div className="card__hint" style={{ marginTop: 5 }}>
                  {aiBusy ? "Mari is restyling… config-only, no code (can take up to a minute)" : aiNote ?? "Mari edits the theme config only — preset, accent, radius, density, mode."}
                </div>
              </div>

              <div className="pub-divider">
                <Field label="Generator" hint="Persisted on the site — deploys and rebuilds reuse it.">
                  <Select value={generator} onChange={(e) => setGenerator(e.target.value)} aria-label="Site generator">
                    {GENERATORS.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
                  </Select>
                </Field>
              </div>

              <Button block style={{ marginTop: 12 }} disabled={building} onClick={rebuild}>
                <Ic.Refresh size={13} /> {building ? "Rebuilding…" : "Rebuild preview"}
              </Button>
              {buildErr && <div className="card__hint pub-err" role="alert" style={{ marginTop: 6 }}>{buildErr}</div>}
              {buildInfo && (
                <div className="card__hint" style={{ marginTop: 6 }}>
                  {buildInfo.msg}{" "}
                  {buildInfo.url && (
                    <a className="linklike" href={buildInfo.url} target="_blank" rel="noreferrer">
                      Open site <Ic.External size={11} />
                    </a>
                  )}
                </div>
              )}
            </>
          )}

          {tab === "deploy" && (
            <>
              <SectionLabel className="pub-sec">S3 target</SectionLabel>
              <Input value={bucket} placeholder={suggestedBucket ? `Bucket — e.g. ${suggestedBucket}` : "Bucket name"} onChange={(e) => setBucket(e.target.value)} />
              <Input value={region} placeholder="Region — e.g. us-west-2" onChange={(e) => setRegion(e.target.value)} style={{ marginTop: 6 }} />
              <SectionLabel className="pub-sec">Domain mapping</SectionLabel>
              <Input value={cname} placeholder="CNAME record notes" onChange={(e) => setCname(e.target.value)} />
              <div className="card__hint" style={{ marginTop: 5 }}>
                Point {site.domain} → your S3 website endpoint (CNAME). Without a bucket, deploys build locally.
              </div>
              <Button compact style={{ marginTop: 8 }} disabled={savingCfg} onClick={saveCfg}>
                {savingCfg ? "Saving…" : savedCfg ? <><Ic.Check size={12} /> Saved</> : "Save deploy config"}
              </Button>

              <div className="pub-divider">
                <SectionLabel>Current release</SectionLabel>
                <div className="row" style={{ gap: 8, marginTop: 6 }}>
                  <span className="pub-num pub-num--lg">{live?.version ?? "—"}</span>
                  <span className="card__hint">{live ? fmtDeployed(live.deployed) : releasesQuery.loading ? "loading…" : "never deployed"}</span>
                  {live && (
                    <>
                      <span className="card__spacer" />
                      <StatusChip status="running" label={live.status === "live" ? "Live" : cap(live.status)} />
                    </>
                  )}
                </div>
                {live?.notes && <div className="card__hint" style={{ marginTop: 2 }}>{live.notes}</div>}

                <div className="pub-steps">
                  <Stepper labels={["Build", "Upload", "Release"]} current={deployStep} ariaLabel="Deploy pipeline" />
                </div>
                <div className="card__hint" style={{ marginTop: 2 }}>
                  Build always runs; Upload needs an S3 bucket — without one the release stays a local build.
                </div>

                <Button variant="primary" block style={{ marginTop: 10 }} disabled={deploying} onClick={deploy}>
                  <Ic.Send size={13} /> {deploying ? "Deploying…" : "Deploy"}
                </Button>
                {deployNote && <div className="card__hint" style={{ textAlign: "center", marginTop: 5 }}>{deployNote}</div>}
              </div>

              <div className="pub-divider">
                <SectionLabel>Release history</SectionLabel>
                {history.map((r) => (
                  <div key={r.id} className="pub-rel">
                    <div className="row" style={{ gap: 8 }}>
                      <span className="pub-dot" style={{ background: relDot(r.status) }} />
                      <b>{r.version}</b>
                      <span className="card__hint">{fmtDeployed(r.deployed)}</span>
                      <span className="card__spacer" />
                      <Button compact className="pub-opt-sm" disabled={rollingBack === r.id} onClick={() => rollback(r.id)}>
                        {rollingBack === r.id ? "Rolling back…" : "Rollback"}
                      </Button>
                    </div>
                    {r.notes && <div className="card__hint pub-rel__notes">{r.notes}</div>}
                  </div>
                ))}
                {history.length === 0 && (
                  <div className="card__hint" style={{ marginTop: 6 }}>
                    {releasesQuery.loading ? "Loading release history…" : releasesQuery.error && !releasesQuery.data ? "API offline — release history unavailable." : "No previous releases."}
                  </div>
                )}
              </div>
            </>
          )}
        </Card>

        {/* right: iframe of the REAL built site */}
        <Card
          variant="flush"
          className="pub-preview"
          icon={<span className="sync__dot" aria-hidden="true" />}
          title="Live preview"
          hint={<span className="mono">{site.domain}</span>}
          actions={built ? (
            <a className="linklike" style={{ fontSize: 12.5 }} href={sitePath(site.id)} target="_blank" rel="noreferrer">
              Open in new tab <Ic.External size={11} />
            </a>
          ) : undefined}
        >
          {dirty && built && (
            <div className="pub-rebuild">
              <span>Theme changed — rebuild to see it in the preview.</span>
              <span className="card__spacer" />
              <Button compact className="pub-opt-sm" disabled={building} onClick={rebuild}>
                {building ? "Rebuilding…" : "Rebuild"}
              </Button>
            </div>
          )}

          {built ? (
            <iframe key={buildNonce} className="pub-iframe" src={sitePath(site.id)} title={`${site.name} preview`} />
          ) : (
            <div className="pub-placeholder">
              <span style={{ textAlign: "center" }}>
                <span className="pub-placeholder__title">
                  {built === null ? "Checking for a build…" : "Not built yet"}
                </span>
                <span className="card__hint pub-placeholder__hint">
                  Build the static site from your {site.docs} matching docs.
                </span>
                {built === false && (
                  <Button variant="primary" disabled={building} onClick={rebuild}>
                    {building ? "Building…" : "Build preview"}
                  </Button>
                )}
                {buildErr && <span className="card__hint pub-err" role="alert" style={{ display: "block", marginTop: 8 }}>{buildErr}</span>}
              </span>
            </div>
          )}

          <div className="card__hint pub-preview__foot">
            The built site ships with a floating ✎ Customize menu — open the site in a new tab to tweak it in place.
          </div>
        </Card>
      </div>
    </>
  );
}

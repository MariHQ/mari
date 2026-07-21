// Welcome — the real onboarding wizard. Every step does actual work:
//   1 Connect  — catalog-driven connector grid (GET /connectors/catalog, local
//                same-shape fallback while that endpoint lands). GitHub uses the
//                real repo-picker flow, Upload posts to /onboard/upload, every
//                other provider gets a dynamic credential form with
//                validate/connect against the connector API. No fake OAuth, no
//                "coming soon" — failures surface the server's error verbatim.
//   2 Guide    — the Library's guide packs as radio cards; the choice persists
//                to the `onboarding` settings key AND Library's
//                localStorage("mari.styleguide") convention.
//   3 Glossary — /onboard/glossary-harvest review checklist → upsertGlossary.
//   4 Finish   — a truthful summary built from real session state (live
//                syncStatus polling for running syncs) and a real finalization:
//                updateSetting("onboarding", { …, done: true }).

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as Ic from "../components/icons";
import { Button, Chip, Spinner, Stepper, useToast } from "../components/ui";
import { gql } from "../lib/api";
import { saveSetting } from "./settings/shared";
import {
  CatalogProvider, ProviderGlyph, SessionConnection, TOP_PROVIDERS, useCatalog,
} from "./welcome/catalog";
import { GenericConnect } from "./welcome/GenericConnect";
import { GithubConnect } from "./welcome/GithubConnect";
import { GlossaryStep } from "./welcome/GlossaryStep";
import { GUIDE_PACKS, GuideStep, SCRATCH_ID, guideName } from "./welcome/GuideStep";
import { SyncSummaryLine } from "./welcome/SyncPanel";
import { UploadConnect, UploadedFile } from "./welcome/UploadConnect";
import "./welcome.css";

const STEPS = ["Welcome", "Connect knowledge", "Style guide", "Glossary", "Finish"];

type Pulse = { provider: string; name: string; status: string };
type SlackBot = { checked: boolean; configured: boolean; teamName?: string | null };

/* ————————————————— step 1: connector grid ————————————————— */

function ConnectStep({ catalogLive, providers, connectedKeys, onOpen }: {
  catalogLive: boolean;
  providers: CatalogProvider[];
  connectedKeys: Set<string>;
  onOpen: (p: CatalogProvider) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const rank = (k: string) => { const i = TOP_PROVIDERS.indexOf(k); return i === -1 ? TOP_PROVIDERS.length : i; };
  const ordered = [...providers].sort((a, b) => rank(a.key) - rank(b.key));
  const top = ordered.filter((p) => TOP_PROVIDERS.includes(p.key));
  const rest = ordered.filter((p) => !TOP_PROVIDERS.includes(p.key));
  const shown = showAll ? [...top, ...rest] : top;

  return (
    <>
      <div className="wc-tiles">
        {shown.map((p) => {
          const connected = p.connected || connectedKeys.has(p.key);
          return (
            <button key={p.key} type="button" className={`wc-tile${connected ? " wc-tile--connected" : ""}`} onClick={() => onOpen(p)}>
              <span className="wc-tile__icon"><ProviderGlyph provider={p.key} name={p.name} size={26} /></span>
              <span className="wc-tile__body">
                <b>{p.name}</b>
                <small>{p.blurb}</small>
              </span>
              <span className="wc-tile__state">
                {connected ? <Chip tone="green" dot caps>Connected</Chip> : <span className="wc-tile__cta">Connect <Ic.ArrowR size={12} /></span>}
              </span>
            </button>
          );
        })}
      </div>
      {rest.length > 0 && (
        <div className="wc-showall">
          <Button compact onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
            {showAll ? <><Ic.Chev size={13} style={{ transform: "rotate(180deg)" }} /> Show fewer</> : <><Ic.Chev size={13} /> Show all {providers.length} connectors</>}
          </Button>
        </div>
      )}
      {!catalogLive && (
        <div className="wc-note" role="note">
          <Ic.Globe size={14} />
          <span>
            The connector catalog endpoint (<span className="mono">/connectors/catalog</span>) isn’t reachable, so this is the
            built-in provider list. Connecting still talks to the server directly — if a provider’s backend isn’t live yet,
            you’ll see its real error here, not a fake success.
          </span>
        </div>
      )}
    </>
  );
}

/* ————————————————— page ————————————————— */

export default function WelcomePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [step, setStep] = useState(0);

  // — connectors —
  const catalog = useCatalog();
  const [pulse, setPulse] = useState<Pulse[] | null>(null);
  useEffect(() => {
    let alive = true;
    gql<{ sourcePulse: Pulse[] }>("{ sourcePulse { provider name status } }").then((d) => {
      if (alive && d) setPulse(d.sourcePulse);
    });
    return () => { alive = false; };
  }, []);
  const [connections, setConnections] = useState<SessionConnection[]>([]);
  const [uploads, setUploads] = useState<UploadedFile[]>([]);
  const [drawer, setDrawer] = useState<CatalogProvider | null>(null);

  // Providers connected before this session (catalog flag or live source pulse).
  const connectedKeys = useMemo(() => {
    const keys = new Set(connections.map((c) => c.provider));
    for (const p of pulse ?? []) if (p.status !== "paused") keys.add(p.provider);
    return keys;
  }, [connections, pulse]);

  const addConnection = (c: SessionConnection) =>
    setConnections((cur) => (cur.some((x) => x.sourceId === c.sourceId) ? cur : [...cur, c]));

  // — style guide (loaded from the saved onboarding setting) —
  const [onboarding, setOnboarding] = useState<Record<string, unknown>>({});
  const [guide, setGuide] = useState<string | null>(null);
  const [guideSaving, setGuideSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    gql<{ settings: { key: string; value: any }[] }>("{ settings { key value } }").then((d) => {
      if (!alive) return;
      const v = d?.settings?.find((s) => s.key === "onboarding")?.value;
      if (v && typeof v === "object") {
        setOnboarding(v);
        if (typeof v.guide === "string" && (v.guide === SCRATCH_ID || GUIDE_PACKS.some((g) => g.id === v.guide))) setGuide(v.guide);
      }
    });
    return () => { alive = false; };
  }, []);

  const pickGuide = async (id: string) => {
    const prev = guide;
    setGuide(id);
    setGuideSaving(true);
    const next = { ...onboarding, guide: id };
    const ok = await saveSetting("onboarding", next);
    setGuideSaving(false);
    if (ok) {
      setOnboarding(next);
      if (id !== SCRATCH_ID) {
        try { localStorage.setItem("mari.styleguide", id); } catch { /* Library falls back to its own default */ }
      }
    } else {
      setGuide(prev);
      toast("Save failed — the server rejected the settings write", "error");
    }
  };

  // — glossary —
  const [glossaryAdded, setGlossaryAdded] = useState(0);
  const lastSourceId = connections.length > 0 ? connections[connections.length - 1].sourceId : null;

  // — finish —
  const [slackBot, setSlackBot] = useState<SlackBot>({ checked: false, configured: false });
  useEffect(() => {
    if (step !== STEPS.length - 1 || slackBot.checked) return;
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/bots/status");
        const isJson = (r.headers.get("content-type") ?? "").includes("json");
        if (!r.ok || !isJson) throw new Error();
        const j = await r.json();
        if (alive) setSlackBot({ checked: true, configured: !!j?.slack?.configured, teamName: j?.slack?.teamName });
      } catch {
        if (alive) setSlackBot({ checked: true, configured: false, teamName: null });
      }
    })();
    return () => { alive = false; };
  }, [step, slackBot.checked]);

  const [finish, setFinish] = useState<"idle" | "saving" | "done" | "failed">("idle");
  const finishSetup = async () => {
    if (finish === "saving" || finish === "done") return;
    setFinish("saving");
    const next = {
      ...onboarding,
      guide,
      connected: connections.map((c) => ({ provider: c.provider, sourceId: c.sourceId, detail: c.detail })),
      glossaryTermsAdded: glossaryAdded,
      done: true,
      completedAt: new Date().toISOString(),
    };
    const ok = await saveSetting("onboarding", next);
    if (ok) { setOnboarding(next); setFinish("done"); }
    else { setFinish("failed"); }
  };

  const goTo = (target: number) => setStep(Math.max(0, Math.min(STEPS.length - 1, target)));

  /* — step bodies — */

  const finishBody = () => {
    const anySync = connections.some((c) => c.polls);
    const preExisting = [...connectedKeys].filter((k) => !connections.some((c) => c.provider === k));
    return (
      <div className="welcome-step">
        <div className="welcome-step__head">
          <span className="eyebrow">Step 4</span>
          <h2>Here’s what actually happened.</h2>
          <p>Every line below reflects real server state — nothing is assumed{anySync ? "; running syncs update live" : ""}.</p>
        </div>

        <div className="wc-summary">
          <div className="wc-sum-row">
            <span className="wc-sum-row__icon"><Ic.Layers size={16} /></span>
            <div className="wc-sum-row__body">
              <b>Sources</b>
              {connections.length === 0 ? (
                <span className="card__hint">
                  No new sources connected this session.
                  {preExisting.length > 0 && <> Already syncing: {preExisting.join(", ")}.</>}
                </span>
              ) : (
                <ul className="wc-sum-sources">
                  {connections.map((c) => (
                    <li key={c.sourceId}>
                      <ProviderGlyph provider={c.provider} name={c.name} size={15} />
                      <b>{c.name}</b> <span className="mono">{c.detail}</span>{" "}
                      {c.polls
                        ? <SyncSummaryLine sourceId={c.sourceId} />
                        : <span className="card__hint">
                            {uploads.filter((f) => f.docId).length} docs · {uploads.reduce((n, f) => n + f.chunks, 0)} chunks ·{" "}
                            {uploads.reduce((n, f) => n + f.embedded, 0)} embedded
                          </span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="wc-sum-row">
            <span className="wc-sum-row__icon"><Ic.Quill size={16} /></span>
            <div className="wc-sum-row__body">
              <b>Style guide</b>
              <span className="card__hint">
                {guide ? <>“{guideName(guide)}” saved to workspace settings{guide !== SCRATCH_ID ? " and set as the Library default" : ""}.</> : "Not chosen — you can pick one in the Library any time."}
              </span>
            </div>
          </div>

          <div className="wc-sum-row">
            <span className="wc-sum-row__icon"><Ic.Book size={16} /></span>
            <div className="wc-sum-row__body">
              <b>Glossary</b>
              <span className="card__hint">
                {glossaryAdded > 0 ? `${glossaryAdded} term${glossaryAdded === 1 ? "" : "s"} added via review.` : "Skipped — no terms were added."}
              </span>
            </div>
          </div>

          <div className="wc-sum-row">
            <span className="wc-sum-row__icon"><Ic.Chat size={16} /></span>
            <div className="wc-sum-row__body">
              <b>Slack bot</b>
              <span className="card__hint">
                {!slackBot.checked ? "checking…" : slackBot.configured
                  ? <>Configured{slackBot.teamName ? <> for <b>{slackBot.teamName}</b></> : null}.</>
                  : "Not set up — optional, available under Settings → Sources → Bots."}
              </span>
            </div>
          </div>
        </div>

        {finish !== "done" ? (
          <div className="wc-finish-cta">
            <Button variant="primary" disabled={finish === "saving"} onClick={() => void finishSetup()}>
              {finish === "saving" ? <><Spinner size="sm" /> Saving…</> : <>Finish setup <Ic.CheckCircle size={14} /></>}
            </Button>
            <span className="card__hint">
              Finishing saves the summary above to the <span className="mono">onboarding</span> workspace setting
              {anySync ? " — running syncs keep going on the server" : ""}.
            </span>
            {finish === "failed" && <div className="wc-error" role="alert">Save failed — the server rejected the settings write. Nothing was marked complete.</div>}
          </div>
        ) : (
          <>
            <div className="wc-ok wc-finish-saved"><Ic.CheckCircle size={15} /> Setup saved{anySync ? " — syncs continue in the background" : ""}.</div>
            <div className="wc-next">
              {[
                { to: "/knowledge", icon: <Ic.Book size={17} />, title: "Open Knowledge", sub: "Browse and search everything that just synced" },
                { to: "/lineage", icon: <Ic.LineageIcon size={17} />, title: "Open Lineage", sub: "See how documents connect and derive" },
                { to: "/settings/sources", icon: <Ic.Chat size={17} />, title: "Set up bots", sub: "Slack answers and instant GitHub re-sync" },
              ].map((c) => (
                <button key={c.to} className="wc-next__card" onClick={() => navigate(c.to)}>
                  <span className="wc-next__icon">{c.icon}</span>
                  <span><b>{c.title}</b><small>{c.sub}</small></span>
                  <Ic.ArrowR size={14} />
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    );
  };

  return (
    <div className="welcome-page">
      <div className="welcome-topline">
        <span className="eyebrow">Workspace setup</span>
        <button className="linklike" onClick={() => navigate("/")}>Save and finish later</button>
      </div>
      <Stepper labels={STEPS} current={step} onSelect={goTo} />

      <section className="card welcome-card">
        {step === 0 && (
          <div className="welcome-hero">
            <div className="welcome-hero__copy">
              <span className="eyebrow">Welcome to Mari Cloud</span>
              <h2>Turn scattered context into trusted knowledge.</h2>
              <p>We’ll connect where your team already works, set a shared editorial baseline, and seed a glossary from your own documents — all for real, with live progress you can watch.</p>
              <ul>
                <li><Ic.CheckCircle size={16} /> Sources sync through the real ingestion pipeline</li>
                <li><Ic.CheckCircle size={16} /> Your style guide choice becomes the Library default</li>
                <li><Ic.CheckCircle size={16} /> Glossary terms are proposed from your docs, reviewed by you</li>
              </ul>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="welcome-step">
            <div className="welcome-step__head">
              <span className="eyebrow">Step 1</span>
              <h2>Where does your team’s knowledge live?</h2>
              <p>Connect one source to start — you can add more or tune sync scope any time under Settings → Sources.</p>
            </div>
            {catalog.loading ? (
              <div className="wc-center"><Spinner size="sm" /> Loading connector catalog…</div>
            ) : (
              <ConnectStep
                catalogLive={catalog.live}
                providers={catalog.providers}
                connectedKeys={connectedKeys}
                onOpen={setDrawer}
              />
            )}
          </div>
        )}

        {step === 2 && (
          <div className="welcome-step">
            <div className="welcome-step__head">
              <span className="eyebrow">Step 2</span>
              <h2>Choose a style guide.</h2>
              <p>The same packs the Library offers — your pick becomes the project default there, and Mari layers your project voice on top. {guideSaving && <Spinner size="sm" />}</p>
            </div>
            <GuideStep guide={guide} saving={guideSaving} onPick={(id) => void pickGuide(id)} openLibrary={() => navigate("/library?tab=guides")} />
            <div className="wc-row wc-under">
              <button className="linklike" onClick={() => navigate("/library?tab=guides")}>Manage guides in the Library <Ic.ArrowR size={12} /></button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="welcome-step">
            <div className="welcome-step__head">
              <span className="eyebrow">Step 3</span>
              <h2>Seed your glossary.</h2>
              <p>Optional — scan your documents for terms a new teammate would need defined, then review before anything is saved.</p>
            </div>
            <GlossaryStep sourceId={lastSourceId} added={glossaryAdded} onAdded={setGlossaryAdded} />
          </div>
        )}

        {step === 4 && finishBody()}

        <footer className="welcome-card__foot">
          <span>{step > 0 ? <Button onClick={() => goTo(step - 1)}><Ic.ChevL size={14} /> Back</Button> : <span />}</span>
          <span className="welcome-card__progress">{step + 1} of {STEPS.length}</span>
          {step < STEPS.length - 1 ? (
            <Button variant="primary" onClick={() => goTo(step + 1)}>
              {step === 0 ? "Set up my workspace" : step === 3 && glossaryAdded === 0 ? "Skip for now" : "Continue"} <Ic.ArrowR size={14} />
            </Button>
          ) : finish === "done" ? (
            <Button variant="primary" onClick={() => navigate("/")}>Go to Overview <Ic.ArrowR size={14} /></Button>
          ) : (
            <Button variant="primary" disabled={finish === "saving"} onClick={() => void finishSetup()}>
              {finish === "saving" ? "Saving…" : <>Finish setup <Ic.CheckCircle size={14} /></>}
            </Button>
          )}
        </footer>
      </section>

      {drawer?.key === "github" && (
        <GithubConnect onClose={() => setDrawer(null)} onConnected={addConnection} />
      )}
      {drawer?.key === "upload" && (
        <UploadConnect
          onClose={() => setDrawer(null)}
          onConnected={(c, files) => { addConnection(c); setUploads((cur) => [...cur, ...files]); }}
        />
      )}
      {drawer && drawer.key !== "github" && drawer.key !== "upload" && (
        <GenericConnect provider={drawer} onClose={() => setDrawer(null)} onConnected={addConnection} />
      )}
    </div>
  );
}

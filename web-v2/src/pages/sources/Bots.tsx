// Bots — self-serve setup for the Slack bot and the GitHub push webhook
// (BOTS-CONTRACT.md §C). Everything here is driven by real server state:
// GET /bots/status (observed events, never assumed), GET /bots/slack/manifest,
// POST /bots/slack/test. Credentials land in the settings keys `slack_bot` /
// `github_bot` through the same updateSetting mutation the Settings pages use
// (helper mirrored from settings/shared.tsx to keep file ownership clean).

import { useCallback, useEffect, useState } from "react";
import * as Ic from "../../components/icons";
import { GitHubMark, SlackMark } from "../../components/icons";
import {
  Button, Card, Chip, Drawer, EmptyState, Field, IconRing, Input,
  SectionLabel, Spinner, StatusChip, Stepper, fmtDateTime, useToast,
} from "../../components/ui";
import { gql } from "../../lib/api";

/* ————————————————— settings helpers (mirror of settings/shared.tsx) ————————————————— */

async function saveSetting(key: string, value: unknown): Promise<boolean> {
  const r = await gql(
    `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`,
    { key, value },
  );
  return !!r;
}

async function loadSetting(key: string): Promise<any> {
  const d = await gql<{ settings: { key: string; value: any }[] }>(`{ settings { key value } }`);
  return d?.settings?.find((s) => s.key === key)?.value ?? null;
}

/* ————————————————— /bots/status ————————————————— */

type BotsStatus = {
  slack: { configured: boolean; teamName?: string | null; lastEventAt?: string | null; lastError?: string | null };
  github: { webhookConfigured: boolean; lastDeliveryAt?: string | null; sources?: { id: number; repo: string }[] };
};

type StatusState = { data: BotsStatus | null; loading: boolean; notFound: boolean; error: string };

function useBotsStatus() {
  const [state, setState] = useState<StatusState>({ data: null, loading: true, notFound: false, error: "" });
  const [nonce, setNonce] = useState(0);
  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/bots/status");
        if (!alive) return;
        // 404 — or the dev server's SPA fallback answering with index.html
        // because /bots isn't proxied — both mean "no bot endpoints here".
        const isJson = (r.headers.get("content-type") ?? "").includes("json");
        if (r.status === 404 || (r.ok && !isJson)) { setState({ data: null, loading: false, notFound: true, error: "" }); return; }
        if (!r.ok) {
          setState((s) => ({ ...s, loading: false, notFound: false, error: `The server answered ${r.status}.` }));
          return;
        }
        const data = (await r.json()) as BotsStatus;
        if (alive) setState({ data, loading: false, notFound: false, error: "" });
      } catch {
        if (alive) setState((s) => ({ ...s, loading: false, notFound: false, error: "The API didn’t answer." }));
      }
    })();
    return () => { alive = false; };
  }, [nonce]);

  return { ...state, refetch };
}

/** Poll /bots/status while a verify step is on screen. */
function useStatusPoll(active: boolean, refetch: () => void, ms = 4000) {
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(refetch, ms);
    return () => window.clearInterval(id);
  }, [active, refetch, ms]);
}

/* ————————————————— small shared bits ————————————————— */

function useCopy() {
  const toast = useToast();
  return useCallback(async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${what} copied to clipboard`, "success");
    } catch {
      toast("Copy failed — select the text and copy it manually", "error");
    }
  }, [toast]);
}

/** The honest status chip: Configured / Waiting for first event / Error / Not set up. */
function BotChip({ configured, hasEvent, error }: { configured: boolean; hasEvent: boolean; error?: string | null }) {
  if (!configured) return <Chip tone="faint" dot>Not set up</Chip>;
  if (error) return <Chip tone="red" dot>Error</Chip>;
  if (!hasEvent) return <Chip tone="gold" dot>Waiting for first event</Chip>;
  return <Chip tone="green" dot>Configured</Chip>;
}

const webhookUrl = () => `${window.location.origin}/webhooks/github`;

/* ————————————————— Slack guided setup (Drawer + 4-step Stepper) ————————————————— */

const SLACK_STEPS = ["Create the app", "Connect", "Verify", "Use it"];

function SlackSetup({ status, refetch, onClose }: { status: BotsStatus; refetch: () => void; onClose: () => void }) {
  const toast = useToast();
  const copy = useCopy();
  const [step, setStep] = useState(0);

  // Step 1 — the app manifest, straight from the server (it fills in the events URL).
  const [manifest, setManifest] = useState<{ text: string; error: string; loading: boolean }>({ text: "", error: "", loading: true });
  const [manifestNonce, setManifestNonce] = useState(0);
  const loadManifest = useCallback(() => {
    setManifest({ text: "", error: "", loading: true });
    setManifestNonce((n) => n + 1);
  }, []);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/bots/slack/manifest");
        if (!alive) return;
        const isHtml = (r.headers.get("content-type") ?? "").includes("text/html");
        if (!r.ok || isHtml) {
          setManifest({
            text: "",
            error: r.status === 404 || isHtml
              ? "the API doesn’t expose /bots/slack/manifest here (in dev, check that the web proxy forwards /bots to the API)."
              : `the server answered ${r.status}.`,
            loading: false,
          });
          return;
        }
        const text = await r.text();
        if (alive) setManifest({ text, error: "", loading: false });
      } catch {
        if (alive) setManifest({ text: "", error: "the API didn’t answer.", loading: false });
      }
    })();
    return () => { alive = false; };
  }, [manifestNonce]);

  // Step 2 — credentials → settings key `slack_bot`.
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedNow, setSavedNow] = useState(false);
  const tokenTyped = token.trim().length > 0;
  const tokenValid = token.trim().startsWith("xoxb-") && token.trim().length > "xoxb-".length;
  const canSave = tokenValid && secret.trim().length > 0 && !saving;
  const canProceedFromConnect = savedNow || status.slack.configured;

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    const ok = await saveSetting("slack_bot", { bot_token: token.trim(), signing_secret: secret.trim() });
    setSaving(false);
    if (ok) {
      setSavedNow(true);
      toast("Slack credentials saved", "success");
      refetch();
    } else {
      toast("Save failed — the server rejected the settings write", "error");
    }
  };

  // Step 3 — POST /bots/slack/test (auth.test with the stored token).
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; team?: string; botUser?: string; error?: string } | null>(null);
  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await fetch("/bots/slack/test", { method: "POST" });
      const j = await r.json().catch(() => null);
      if (j && typeof j.ok === "boolean") setTest(j);
      else setTest({ ok: false, error: `The server answered ${r.status} without a readable result.` });
    } catch {
      setTest({ ok: false, error: "The API didn’t answer." });
    }
    setTesting(false);
    refetch();
  };

  // Step 4 — live status strip.
  useStatusPoll(step === 3, refetch, 5000);

  const body = () => {
    switch (step) {
      case 0:
        return (
          <>
            <h4 className="bots-step-title">Create the Slack app</h4>
            <p className="bots-step-sub">
              The manifest below pre-fills everything — bot user, scopes, and this workspace’s events URL. Nothing to configure by hand.
            </p>
            <ol className="bots-steps">
              <li>Open <a className="linklike" href="https://api.slack.com/apps" target="_blank" rel="noreferrer">api.slack.com/apps <Ic.External size={11} /></a> and click <b>Create New App</b>.</li>
              <li>Choose <b>From a manifest</b>, then pick the workspace Mari should join.</li>
              <li>Paste the manifest (YAML tab) and click <b>Create</b>.</li>
              <li>On the new app’s page, click <b>Install to Workspace</b> and approve.</li>
            </ol>
            {manifest.loading ? (
              <div className="src-loading"><Spinner size="sm" /> Loading manifest…</div>
            ) : manifest.error ? (
              <div className="wiz-error" role="alert">
                Couldn’t load the manifest — {manifest.error}
                <div className="bots-row"><Button compact onClick={loadManifest}><Ic.Refresh size={13} /> Retry</Button></div>
              </div>
            ) : (
              <div className="bots-manifest">
                <div className="bots-manifest__bar">
                  <SectionLabel>App manifest · YAML</SectionLabel>
                  <Button compact onClick={() => copy(manifest.text, "Manifest")}><Ic.Clipboard size={13} /> Copy manifest</Button>
                </div>
                <pre className="mono bots-manifest__pre">{manifest.text}</pre>
              </div>
            )}
          </>
        );
      case 1:
        return (
          <>
            <h4 className="bots-step-title">Connect the app</h4>
            <p className="bots-step-sub">
              Both values are on your app’s page at api.slack.com. They’re stored server-side and never shown again.
            </p>
            <div className="bots-fields">
              <Field label="Bot token" hint="OAuth & Permissions → Bot User OAuth Token — appears after you install the app. Starts with xoxb-.">
                <Input type="password" autoComplete="off" placeholder="xoxb-…" value={token} onChange={(e) => setToken(e.target.value)} />
              </Field>
              {tokenTyped && !tokenValid && (
                <div className="bots-err" role="alert">
                  That doesn’t look like a bot token — it must start with <span className="mono">xoxb-</span>.
                  (User tokens start with <span className="mono">xoxp-</span> and won’t work here.)
                </div>
              )}
              <Field label="Signing secret" hint="Basic Information → App Credentials → Signing Secret — lets Mari verify events really come from Slack.">
                <Input type="password" autoComplete="off" placeholder="Signing secret" value={secret} onChange={(e) => setSecret(e.target.value)} />
              </Field>
            </div>
            <div className="bots-row">
              <Button variant="primary" disabled={!canSave} onClick={save}>
                {saving ? <><Spinner size="sm" /> Saving…</> : <><Ic.Key size={13} /> Save credentials</>}
              </Button>
              {savedNow && <span className="wiz-auth__ok"><Ic.CheckCircle size={14} /> Saved</span>}
              {!savedNow && status.slack.configured && (
                <span className="card__hint">Credentials are already saved — enter new values only to replace them.</span>
              )}
            </div>
          </>
        );
      case 2:
        return (
          <>
            <h4 className="bots-step-title">Verify the connection</h4>
            <p className="bots-step-sub">
              Runs Slack’s <span className="mono">auth.test</span> with the saved bot token — proof that Mari can talk to your workspace.
            </p>
            <div className="bots-row">
              <Button variant="primary" disabled={testing} onClick={runTest}>
                {testing ? <><Spinner size="sm" /> Testing…</> : <><Ic.ShieldCheck size={14} /> Test connection</>}
              </Button>
            </div>
            {test && (test.ok ? (
              <div className="bots-strip">
                <StatusChip status="approved" label="Connected" />
                <span>Workspace <b>{test.team || "—"}</b> · bot user <b>{test.botUser || "—"}</b></span>
              </div>
            ) : (
              <div className="wiz-error" role="alert">
                <b>Slack test failed.</b> {test.error || "The server reported a failure without details."}
                <div className="bots-row"><Button compact onClick={runTest}><Ic.Refresh size={13} /> Retry</Button></div>
              </div>
            ))}
          </>
        );
      default:
        return (
          <>
            <h4 className="bots-step-title">Use it</h4>
            <p className="bots-step-sub">You’re done — the rest happens in Slack.</p>
            <ul className="bots-steps">
              <li>Invite the bot to a channel (<span className="mono">/invite @Mari</span>) and <b>@mention</b> it with a question.</li>
              <li>Or open a <b>DM</b> with the bot and just ask.</li>
              <li>Answers come from this workspace’s knowledge base — the same retrieval that powers Ask.</li>
            </ul>
            <div className="bots-strip">
              <BotChip configured={status.slack.configured} hasEvent={!!status.slack.lastEventAt} error={status.slack.lastError} />
              <span>
                {status.slack.lastEventAt
                  ? <>Last event {fmtDateTime(status.slack.lastEventAt)}</>
                  : "No events yet — mention the bot in Slack and it lands here."}
              </span>
            </div>
            {status.slack.lastError && <div className="bots-err" role="alert">Last error: {status.slack.lastError}</div>}
            <span className="card__hint">Live status — refreshes every few seconds while this panel is open.</span>
          </>
        );
    }
  };

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={<span className="bots-drawer-title"><SlackMark size={18} /> Set up the Slack bot</span>}
      width={620}
      footer={
        <div className="bots-foot">
          <Button disabled={step === 0} onClick={() => setStep((n) => Math.max(0, n - 1))}><Ic.ChevL size={13} /> Back</Button>
          {step < SLACK_STEPS.length - 1 ? (
            <Button
              variant="primary"
              disabled={step === 1 && !canProceedFromConnect}
              title={step === 1 && !canProceedFromConnect ? "Save the bot token and signing secret first" : undefined}
              onClick={() => setStep((n) => n + 1)}
            >
              Next <Ic.ChevR size={13} />
            </Button>
          ) : (
            <Button variant="primary" onClick={onClose}>Done <Ic.CheckCircle size={14} /></Button>
          )}
        </div>
      }
    >
      <div className="bots-drawer-steps">
        <Stepper labels={SLACK_STEPS} current={step} ariaLabel="Slack bot setup progress" onSelect={(i) => { if (i < step) setStep(i); }} />
      </div>
      {body()}
    </Drawer>
  );
}

/* ————————————————— GitHub webhook guided setup (Drawer + 3-step Stepper) ————————————————— */

const GH_STEPS = ["Add the webhook", "Secret", "Verify"];

const randomHexSecret = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(24)), (b) => b.toString(16).padStart(2, "0")).join("");

function GithubSetup({ status, refetch, onClose }: { status: BotsStatus; refetch: () => void; onClose: () => void }) {
  const toast = useToast();
  const copy = useCopy();
  const [step, setStep] = useState(0);
  const url = webhookUrl();

  // Step 2 — secret → settings key `github_bot`. Prefill from the saved
  // setting so a returning user can re-copy the exact secret GitHub expects.
  const [ghSecret, setGhSecret] = useState("");
  const [savedNow, setSavedNow] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    loadSetting("github_bot").then((v) => {
      if (alive && v?.webhook_secret) setGhSecret((cur) => cur || String(v.webhook_secret));
    });
    return () => { alive = false; };
  }, []);
  const canProceedFromSecret = savedNow || status.github.webhookConfigured;

  const save = async () => {
    if (!ghSecret.trim() || saving) return;
    setSaving(true);
    const ok = await saveSetting("github_bot", { webhook_secret: ghSecret.trim(), repo_events: true });
    setSaving(false);
    if (ok) {
      setSavedNow(true);
      toast("Webhook secret saved", "success");
      refetch();
    } else {
      toast("Save failed — the server rejected the settings write", "error");
    }
  };

  // Step 3 — poll for a real delivery.
  useStatusPoll(step === 2, refetch, 4000);
  const repos = status.github.sources ?? [];

  const body = () => {
    switch (step) {
      case 0:
        return (
          <>
            <h4 className="bots-step-title">Add the webhook on GitHub</h4>
            <p className="bots-step-sub">
              A push webhook re-syncs connected repositories the moment commits land — instead of waiting for the background poll.
            </p>
            <div className="src-kv"><span className="src-kv__k">Payload URL</span><span className="src-kv__v bots-url"><span className="mono">{url}</span><Button compact onClick={() => copy(url, "Webhook URL")}><Ic.Clipboard size={13} /> Copy</Button></span></div>
            <div className="src-kv"><span className="src-kv__k">Content type</span><span className="src-kv__v mono">application/json</span></div>
            <div className="src-kv"><span className="src-kv__k">Events</span><span className="src-kv__v">Just the push event</span></div>
            <ol className="bots-steps">
              <li>Open your repository on GitHub → <b>Settings</b> → <b>Webhooks</b> → <b>Add webhook</b>.</li>
              <li>Paste the payload URL and set content type to <span className="mono">application/json</span>.</li>
              <li>Under events, pick <b>Just the push event</b>. Leave the secret empty for now — the next step generates one.</li>
            </ol>
          </>
        );
      case 1:
        return (
          <>
            <h4 className="bots-step-title">Set a shared secret</h4>
            <p className="bots-step-sub">
              GitHub signs every delivery with this secret and Mari verifies the signature — so nobody else can trigger a sync.
            </p>
            <div className="bots-fields">
              <Field label="Webhook secret" hint="Generate one (recommended) or paste your own, then save it here and in GitHub.">
                <Input className="src-input--mono" autoComplete="off" placeholder="Click Generate…" value={ghSecret} onChange={(e) => { setGhSecret(e.target.value); setSavedNow(false); }} />
              </Field>
            </div>
            <div className="bots-row">
              <Button onClick={() => { setGhSecret(randomHexSecret()); setSavedNow(false); }}><Ic.Sparkle size={13} /> Generate</Button>
              <Button disabled={!ghSecret.trim()} onClick={() => copy(ghSecret.trim(), "Webhook secret")}><Ic.Clipboard size={13} /> Copy</Button>
              <Button variant="primary" disabled={!ghSecret.trim() || saving} onClick={save}>
                {saving ? <><Spinner size="sm" /> Saving…</> : <><Ic.Key size={13} /> Save secret</>}
              </Button>
              {savedNow && <span className="wiz-auth__ok"><Ic.CheckCircle size={14} /> Saved</span>}
              {!savedNow && status.github.webhookConfigured && (
                <span className="card__hint">A secret is already saved — generate a new one only to rotate it.</span>
              )}
            </div>
            <ol className="bots-steps">
              <li>Copy the secret, then open the webhook you added on GitHub (repo → <b>Settings</b> → <b>Webhooks</b> → edit).</li>
              <li>Paste it into the <b>Secret</b> field and click <b>Update webhook</b>.</li>
            </ol>
            <span className="card__hint">
              Running the webhook receiver as a separate process? Set <span className="mono">MARI_GITHUB_WEBHOOK_SECRET</span> in that server’s environment to the same value.
            </span>
          </>
        );
      default:
        return (
          <>
            <h4 className="bots-step-title">Verify with a real delivery</h4>
            <p className="bots-step-sub">
              In the GitHub webhook’s <b>Recent Deliveries</b> tab, deliver a test ping (GitHub also sends one automatically when the webhook is created). This panel polls until one lands.
            </p>
            {status.github.lastDeliveryAt ? (
              <div className="bots-strip">
                <StatusChip status="approved" label="Delivery received" />
                <span>Last delivery {fmtDateTime(status.github.lastDeliveryAt)}</span>
              </div>
            ) : (
              <div className="bots-strip">
                <Spinner size="sm" />
                <span>Waiting for the first delivery — checking every few seconds…</span>
              </div>
            )}
            {repos.length > 0 ? (
              <>
                <span className="card__hint">Pushes to these connected repositories trigger an instant re-sync:</span>
                <div className="src-chips bots-row">
                  {repos.map((r) => <Chip key={r.id} icon={<Ic.Fork size={12} />}>{r.repo}</Chip>)}
                </div>
              </>
            ) : (
              <span className="card__hint">
                No GitHub repositories are connected yet — the webhook still verifies, but connect a repository on the Connectors tab so pushes have something to sync.
              </span>
            )}
          </>
        );
    }
  };

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={<span className="bots-drawer-title"><GitHubMark size={18} /> Set up the GitHub webhook</span>}
      width={620}
      footer={
        <div className="bots-foot">
          <Button disabled={step === 0} onClick={() => setStep((n) => Math.max(0, n - 1))}><Ic.ChevL size={13} /> Back</Button>
          {step < GH_STEPS.length - 1 ? (
            <Button
              variant="primary"
              disabled={step === 1 && !canProceedFromSecret}
              title={step === 1 && !canProceedFromSecret ? "Save the webhook secret first" : undefined}
              onClick={() => setStep((n) => n + 1)}
            >
              Next <Ic.ChevR size={13} />
            </Button>
          ) : (
            <Button variant="primary" onClick={onClose}>Done <Ic.CheckCircle size={14} /></Button>
          )}
        </div>
      }
    >
      <div className="bots-drawer-steps">
        <Stepper labels={GH_STEPS} current={step} ariaLabel="GitHub webhook setup progress" onSelect={(i) => { if (i < step) setStep(i); }} />
      </div>
      {body()}
    </Drawer>
  );
}

/* ————————————————— the Bots tab ————————————————— */

export function BotsTab() {
  const status = useBotsStatus();
  const [setup, setSetup] = useState<null | "slack" | "github">(null);

  if (status.loading) {
    return <div className="src-loading"><Spinner size="sm" /> Checking bot status…</div>;
  }
  if (status.notFound) {
    return (
      <div className="card src-panel">
        <EmptyState icon={<Ic.Globe size={22} />} title="Bot endpoints not available">
          The API doesn’t expose <span className="mono">/bots</span> here. Update the Mari Cloud server and
          restart it — and if you’re running the web dev server, make sure it proxies{" "}
          <span className="mono">/bots</span> (and <span className="mono">/webhooks</span>) to the API.
        </EmptyState>
      </div>
    );
  }
  if (!status.data) {
    return (
      <div className="card src-panel">
        <EmptyState
          icon={<Ic.Globe size={22} />}
          title="Couldn’t load bot status"
          action={<Button compact onClick={status.refetch}><Ic.Refresh size={13} /> Retry</Button>}
        >
          {status.error || "The API didn’t answer."} If the server is still starting up, retry in a moment.
        </EmptyState>
      </div>
    );
  }

  const s = status.data;

  return (
    <>
      <div className="bots-cards">
        <Card
          title="Slack bot"
          icon={<IconRing><SlackMark size={16} /></IconRing>}
          actions={<BotChip configured={s.slack.configured} hasEvent={!!s.slack.lastEventAt} error={s.slack.lastError} />}
        >
          <p className="bots-desc">
            Bring Mari into Slack — @mention it in any channel it’s invited to, or DM it,
            and it answers from this workspace’s knowledge base.
          </p>
          <div className="src-kv"><span className="src-kv__k">Workspace</span><span className="src-kv__v">{s.slack.teamName || "—"}</span></div>
          <div className="src-kv"><span className="src-kv__k">Last event</span><span className="src-kv__v">{s.slack.lastEventAt ? fmtDateTime(s.slack.lastEventAt) : "none yet"}</span></div>
          {s.slack.lastError && <div className="bots-err">Last error: {s.slack.lastError}</div>}
          <div className="bots-row">
            <Button variant="primary" onClick={() => setSetup("slack")}>
              {s.slack.configured ? <><Ic.Gear size={13} /> Manage setup</> : <><Ic.Plus size={13} /> Set up Slack bot</>}
            </Button>
          </div>
        </Card>

        <Card
          title="GitHub webhook"
          icon={<IconRing><GitHubMark size={16} /></IconRing>}
          actions={<BotChip configured={s.github.webhookConfigured} hasEvent={!!s.github.lastDeliveryAt} />}
        >
          <p className="bots-desc">
            Instant re-sync on push — GitHub tells Mari the moment commits land,
            so connected repositories update without waiting for the background poll.
          </p>
          <div className="src-kv"><span className="src-kv__k">Payload URL</span><span className="src-kv__v mono">{webhookUrl()}</span></div>
          <div className="src-kv"><span className="src-kv__k">Last delivery</span><span className="src-kv__v">{s.github.lastDeliveryAt ? fmtDateTime(s.github.lastDeliveryAt) : "none yet"}</span></div>
          <div className="src-kv">
            <span className="src-kv__k">Repositories</span>
            <span className="src-kv__v">
              {(s.github.sources ?? []).length > 0
                ? <span className="src-chips">{(s.github.sources ?? []).map((r) => <Chip key={r.id} icon={<Ic.Fork size={12} />}>{r.repo}</Chip>)}</span>
                : "none connected yet"}
            </span>
          </div>
          <div className="bots-row">
            <Button variant="primary" onClick={() => setSetup("github")}>
              {s.github.webhookConfigured ? <><Ic.Gear size={13} /> Manage setup</> : <><Ic.Plus size={13} /> Set up webhook</>}
            </Button>
          </div>
        </Card>
      </div>

      {status.error && (
        <div className="bots-err" role="alert">
          Status refresh failed — {status.error} Showing the last known state.
        </div>
      )}

      {setup === "slack" && <SlackSetup status={s} refetch={status.refetch} onClose={() => { setSetup(null); status.refetch(); }} />}
      {setup === "github" && <GithubSetup status={s} refetch={status.refetch} onClose={() => { setSetup(null); status.refetch(); }} />}
    </>
  );
}

// Generic connector drawer — renders a provider's credential fields from the
// catalog spec (secret → password input), "Test connection" via
// POST /connectors/validate, then POST /connectors/connect → live sync via
// syncStatus polling. Server errors are shown verbatim; nothing is simulated.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Chip, Drawer, Field, Input, Spinner, Textarea } from "../../components/ui";
import type { CatalogProvider, SessionConnection } from "./catalog";
import { ProviderGlyph } from "./catalog";
import { SyncPanel } from "./SyncPanel";

async function post(path: string, body: unknown): Promise<{ status: number; json: any | null }> {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const isJson = (r.headers.get("content-type") ?? "").includes("json");
    return { status: r.status, json: isJson ? await r.json().catch(() => null) : null };
  } catch {
    return { status: 0, json: null };
  }
}

const describeFailure = (status: number, json: any): string => {
  if (json?.error) return String(json.error);
  if (typeof json?.detail === "string") return json.detail;
  if (status === 0) return "The API didn’t answer.";
  if (status === 404) return "The server doesn’t expose the connector API yet (404) — update and restart the Mari Cloud server.";
  return `The server answered ${status} without a readable error.`;
};

export function GenericConnect({ provider, onClose, onConnected }: {
  provider: CatalogProvider;
  onClose: () => void;
  onConnected: (c: SessionConnection) => void;
}) {
  const [config, setConfig] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; error?: string } | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [sourceId, setSourceId] = useState<number | null>(null);

  const filled = provider.fields.every((f) => (config[f.key] ?? "").trim().length > 0);
  const set = (key: string, value: string) => {
    setConfig((c) => ({ ...c, [key]: value }));
    setTest(null);
    setConnectError("");
  };
  const trimmed = () => Object.fromEntries(Object.entries(config).map(([k, v]) => [k, v.trim()]));

  const runTest = async () => {
    if (!filled || testing) return;
    setTesting(true);
    setTest(null);
    const { status, json } = await post("/connectors/validate", { provider: provider.key, config: trimmed() });
    setTesting(false);
    if (json && json.ok === true) setTest({ ok: true });
    else setTest({ ok: false, error: describeFailure(status, json) });
  };

  const connect = async () => {
    if (!filled || connecting) return;
    setConnecting(true);
    setConnectError("");
    const { status, json } = await post("/connectors/connect", { provider: provider.key, config: trimmed() });
    setConnecting(false);
    if (json && typeof json.sourceId === "number") {
      setSourceId(json.sourceId);
      onConnected({ provider: provider.key, name: provider.name, sourceId: json.sourceId, detail: provider.name, polls: true });
    } else {
      setConnectError(describeFailure(status, json));
    }
  };

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o && !connecting) onClose(); }}
      title={<span className="wc-drawer-title"><ProviderGlyph provider={provider.key} name={provider.name} size={18} /> Connect {provider.name}</span>}
      width={560}
      footer={
        <div className="wc-foot">
          {sourceId == null ? (
            <>
              <Button disabled={!filled || testing || connecting} onClick={runTest}>
                {testing ? <><Spinner size="sm" /> Testing…</> : <><Ic.ShieldCheck size={14} /> Test connection</>}
              </Button>
              <Button variant="primary" disabled={!filled || connecting || testing} onClick={connect}>
                {connecting ? <><Spinner size="sm" /> Connecting…</> : <>Connect &amp; sync <Ic.ArrowR size={14} /></>}
              </Button>
            </>
          ) : (
            <>
              <span className="card__hint">Sync continues on the server — closing won’t interrupt it.</span>
              <Button variant="primary" onClick={onClose}>Done <Ic.CheckCircle size={14} /></Button>
            </>
          )}
        </div>
      }
    >
      {sourceId == null ? (
        <>
          <p className="wc-drawer-sub">
            {provider.blurb}{" "}
            {provider.docsUrl && (
              <a className="linklike" href={provider.docsUrl} target="_blank" rel="noreferrer">
                Where do I get these? <Ic.External size={11} />
              </a>
            )}
          </p>
          <div className="wc-fields">
            {provider.fields.map((f) =>
              f.key === "service_account_json" ? (
                <Field key={f.key} label={f.label} hint={f.help}>
                  <Textarea
                    rows={5}
                    className="mono"
                    autoComplete="off"
                    placeholder={f.placeholder}
                    value={config[f.key] ?? ""}
                    onChange={(e) => set(f.key, e.target.value)}
                  />
                </Field>
              ) : (
                <Field key={f.key} label={f.label} hint={f.help}>
                  <Input
                    type={f.secret ? "password" : "text"}
                    autoComplete="off"
                    placeholder={f.placeholder}
                    value={config[f.key] ?? ""}
                    onChange={(e) => set(f.key, e.target.value)}
                  />
                </Field>
              ),
            )}
          </div>
          <span className="card__hint">
            Credentials are stored server-side and never shown again; the catalog only ever returns field names, not values.
          </span>
          {test && (test.ok
            ? <div className="wc-ok wc-row"><Ic.CheckCircle size={14} /> Connection test passed. <Chip tone="green" caps>Valid</Chip></div>
            : <div className="wc-error" role="alert"><b>Connection test failed.</b> {test.error}</div>)}
          {connectError && <div className="wc-error" role="alert"><b>Connect failed.</b> {connectError}</div>}
          {provider.key === "slack" && (
            <div className="wc-note">
              <Ic.Chat size={14} />
              <span>This imports channel history into the library. To have Mari <b>answer questions in Slack</b>, set up the bot under Settings → Sources → Bots.</span>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="wc-drawer-sub">The initial sync runs on the server — live status below.</p>
          <SyncPanel sourceId={sourceId} label={provider.name} />
        </>
      )}
    </Drawer>
  );
}

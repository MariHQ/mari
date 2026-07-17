// Settings → General (workspace identity; library and digest live elsewhere).

import { useState } from "react";
import { Link } from "react-router-dom";
import * as Ic from "../../components/icons";
import {
  Button, Card, Field, Input, PageHeader, Select,
} from "../../components/ui";
import { SavedNote, saveSetting, useSave, useSettings } from "./shared";

const TIMEZONES = [
  "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York",
  "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney", "UTC",
];
const LANGUAGES = ["English (US)", "English (UK)", "Deutsch", "Français", "Español", "日本語"];
const PLANS = ["Consultancy", "Team", "Business", "Enterprise"];

/** Keep the stored value selectable even when it isn't in the canonical list. */
const withCurrent = (options: string[], current: string | undefined) =>
  current && !options.includes(current) ? [current, ...options] : options;

export default function GeneralPage() {
  const [nonce, setNonce] = useState(0);
  const refetch = () => setNonce((n) => n + 1);
  const settings = useSettings(nonce);

  // ——— workspace ———
  const ws = settings.workspace ?? {};
  const [wsDraft, setWsDraft] = useState<Record<string, string>>(ws);
  const [wsDirty, setWsDirty] = useState(false);
  // sync server → draft during render (previous-value pattern, no effect)
  const wsKey = JSON.stringify(ws);
  const [prevWsKey, setPrevWsKey] = useState(wsKey);
  if (wsKey !== prevWsKey) {
    setPrevWsKey(wsKey);
    if (!wsDirty) setWsDraft(ws);
  }
  const setWs = (k: string, v: string) => { setWsDirty(true); setWsDraft((d) => ({ ...d, [k]: v })); };
  const wsSave = useSave();
  const saveWs = () =>
    wsSave.run(async () => {
      await saveSetting("workspace", { ...ws, ...wsDraft });
      setWsDirty(false);
      refetch();
    });

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Workspace"
        description="Workspace identity and language"
      />

      {/* workspace */}
      <Card className="setcard" title="Workspace">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
          <Field label="Workspace name">
            <Input value={wsDraft.name ?? ""} onChange={(e) => setWs("name", e.target.value)} />
          </Field>
          <Field label="Slug">
            <Input
              className="mono"
              value={wsDraft.slug ?? ""}
              onChange={(e) => setWs("slug", e.target.value)}
            />
          </Field>
          <Field label="Plan">
            <Select value={wsDraft.plan ?? PLANS[0]} onChange={(e) => setWs("plan", e.target.value)}>
              {withCurrent(PLANS, wsDraft.plan).map((p) => <option key={p} value={p}>{p}</option>)}
            </Select>
          </Field>
          <Field label="Timezone">
            <Select value={wsDraft.timezone ?? TIMEZONES[0]} onChange={(e) => setWs("timezone", e.target.value)}>
              {withCurrent(TIMEZONES, wsDraft.timezone).map((z) => <option key={z} value={z}>{z}</option>)}
            </Select>
          </Field>
          <Field label="Language">
            <Select value={wsDraft.language ?? LANGUAGES[0]} onChange={(e) => setWs("language", e.target.value)}>
              {withCurrent(LANGUAGES, wsDraft.language).map((l) => <option key={l} value={l}>{l}</option>)}
            </Select>
          </Field>
        </div>
        <div className="row" style={{ gap: 10, marginTop: 16 }}>
          <Button variant="primary" onClick={saveWs} disabled={wsSave.saving || !wsDirty}>
            {wsSave.saving ? "Saving…" : "Save changes"}
          </Button>
          {wsSave.saved && <SavedNote />}
        </div>
      </Card>

      <div className="card setcard editorial-library-link">
        <span className="editorial-library-link__icon"><Ic.Tag size={20} /></span>
        <div>
          <h3>Editorial library</h3>
          <p>Tags, search weights, evidence behavior, style guides, the glossary, and document templates now live together in one shared library.</p>
        </div>
        <Link className="btn" to="/library">Open Library <Ic.ArrowR size={14} /></Link>
      </div>

      <div className="card setcard editorial-library-link">
        <span className="editorial-library-link__icon"><Ic.Clock size={20} /></span>
        <div>
          <h3>Weekly digest</h3>
          <p>The digest and its refresh schedule are now a Flow — edit "Weekly digest refresh" to change when it runs.</p>
        </div>
        <Link className="btn" to="/flows">Open Flows <Ic.ArrowR size={14} /></Link>
      </div>
    </>
  );
}

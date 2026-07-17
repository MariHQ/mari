// Workspace brand editor — bring-your-own-branding. Colors, logo, and display
// font flow through the token layer (src/lib/branding.tsx) — no page in the
// app changes, the primitives simply re-skin. Built only from primitives.
// Exhibited as the "Your brand" section of the Design lookbook (/lookbook).

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import * as Ic from "../../components/icons";
import { gql } from "../../lib/api";
import {
  Button, Card, Chip, ConfirmButton, Field, Input, PageHeader, Spinner, Stat, StatusChip, Swatch, useToast,
} from "../../components/ui";
import {
  computeCssVars, darken, injectFontStylesheet, inkFor, isAllowedFontUrl, isHexColor, LOGO_MAX_BYTES,
  useBranding, type Branding,
} from "../../lib/branding";
import { SavedNote, useSave } from "./shared";

/* The :root defaults — what the color wells show until a brand overrides. */
const DEFAULTS = {
  accent: "#b04e2c",
  accentDeep: "#9c4325",
  accentInk: "#f5ead4",
  green: "#5c7a4c",
  blue: "#35549d",
  gold: "#c8973a",
};

type ColorKey = keyof typeof DEFAULTS;

const COLOR_FIELDS: { key: ColorKey; label: string; hint: string }[] = [
  { key: "accent", label: "Accent", hint: "Sidebar, primary buttons, the underline — replaces terracotta." },
  { key: "accentDeep", label: "Accent · deep", hint: "Hover/pressed accent. Auto-derived unless set." },
  { key: "accentInk", label: "Accent · ink", hint: "Text on the accent. Auto-picked by contrast unless set." },
  { key: "green", label: "Green", hint: "Success, verified, canonical." },
  { key: "blue", label: "Blue", hint: "Links, informational chips." },
  { key: "gold", label: "Gold", hint: "Highlights, warnings-in-amber." },
];

type ImportResult = {
  accent?: string; accentDeep?: string; accentInk?: string;
  green?: string; blue?: string; gold?: string;
  logo?: string; logoAlt?: string; displayFont?: string; bodyFont?: string; fontUrl?: string;
  evidence?: {
    themeColor?: string;
    cssColors?: [string, number][];
    iconUrl?: string;
    fonts?: string[];
    title?: string;
  };
  warnings?: string[];
  error?: string;
};

/** Effective value a color well shows: draft ▸ derived ▸ default. */
function effectiveColor(draft: Branding, key: ColorKey): string {
  const set = draft[key];
  if (isHexColor(set)) return set;
  if (key === "accentDeep" && isHexColor(draft.accent)) return darken(draft.accent, 0.12);
  if (key === "accentInk" && isHexColor(draft.accent)) return inkFor(draft.accent);
  return DEFAULTS[key];
}

export function BrandingEditor() {
  const toast = useToast();
  const { branding, save, reset } = useBranding();

  // ——— draft (edits preview locally; nothing hits :root until Save) ———
  const [draft, setDraft] = useState<Branding>(branding);
  const [dirty, setDirty] = useState(false);
  const serverKey = JSON.stringify(branding);
  const [prevServerKey, setPrevServerKey] = useState(serverKey);
  if (serverKey !== prevServerKey) {
    setPrevServerKey(serverKey);
    if (!dirty) setDraft(branding);
  }
  const patch = (p: Partial<Branding>) => { setDirty(true); setDraft((d) => ({ ...d, ...p })); };

  const saveState = useSave();
  const doSave = () =>
    saveState.run(async () => {
      const ok = await save(draft);
      if (!ok) { toast("Couldn't save branding — is the API up?", "error"); return; }
      setDirty(false);
    });
  const doReset = async () => {
    const ok = await reset();
    if (!ok) { toast("Couldn't reset branding — is the API up?", "error"); return; }
    setDraft({});
    setDirty(false);
    toast("Back to the default Mari look", "success");
  };

  // ——— logo upload (data URI, ≤ ~200KB) ———
  const onLogoFile = (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) { toast("Logo must be an image file", "error"); return; }
    if (file.size > LOGO_MAX_BYTES) { toast("Logo is too large — keep it under 200KB", "error"); return; }
    const reader = new FileReader();
    reader.onload = () => patch({ logo: String(reader.result) });
    reader.readAsDataURL(file);
  };

  // ——— import-your-brand (server mutation importBrand — prefills the draft) ———
  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<ImportResult["evidence"] | null>(null);
  const [importWarnings, setImportWarnings] = useState<string[]>([]);
  const [logoCandidate, setLogoCandidate] = useState<{ logo: string; logoAlt?: string } | null>(null);
  const importHost = useMemo(() => {
    try { return new URL(/^https?:\/\//.test(importUrl) ? importUrl : `https://${importUrl}`).hostname; }
    catch { return importUrl.trim(); }
  }, [importUrl]);

  const runImport = async () => {
    if (!importUrl.trim() || importing) return;
    setImporting(true);
    setImportError(null);
    setEvidence(null);
    setImportWarnings([]);
    setLogoCandidate(null);
    const d = await gql<{ importBrand: ImportResult | null }>(
      `mutation($url: String!) { importBrand(url: $url) }`,
      { url: importUrl.trim() },
    );
    setImporting(false);
    const r = d?.importBrand;
    if (!r || r.error) {
      setImportError(r?.error ?? "Import failed — the server may not support importBrand yet.");
      setImportWarnings(r?.warnings ?? []);
      return; // keep the current draft untouched
    }
    const colors: Partial<Branding> = {};
    (["accent", "accentDeep", "accentInk", "green", "blue", "gold"] as ColorKey[]).forEach((k) => {
      if (isHexColor(r[k])) colors[k] = r[k];
    });
    if (r.displayFont) colors.displayFont = r.displayFont;
    if (r.bodyFont) colors.bodyFont = r.bodyFont;
    if (isAllowedFontUrl(r.fontUrl)) colors.fontUrl = r.fontUrl;
    patch(colors); // draft + live preview only — the user reviews, then saves
    setEvidence(r.evidence ?? null);
    setImportWarnings(r.warnings ?? []);
    if (r.logo) setLogoCandidate({ logo: r.logo, logoAlt: r.logoAlt });
  };

  // ——— live preview: draft tokens scoped to this container only ———
  const previewVars = useMemo(() => computeCssVars(draft) as CSSProperties, [draft]);
  // Load the draft's font stylesheet so the preview renders in it (allow-listed
  // hosts only; fonts are global — the saved brand shares the same link).
  const draftFontUrl = draft.fontUrl;
  useEffect(() => {
    if (draftFontUrl) injectFontStylesheet(draftFontUrl);
  }, [draftFontUrl]);

  return (
    <>
      {/* import your brand */}
      <Card
        className="setcard"
        title="Import your brand"
        hint="Reads your homepage and proposes a palette — nothing is saved until you review it"
      >
        <div className="row" style={{ gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <Field label="Company homepage">
            <Input
              type="url"
              style={{ width: 300 }}
              placeholder="https://acme.com"
              value={importUrl}
              onChange={(e) => setImportUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void runImport(); }}
              disabled={importing}
            />
          </Field>
          <Button variant="primary" style={{ marginBottom: 1 }} onClick={runImport} disabled={importing || !importUrl.trim()}>
            {importing ? <><Spinner size="sm" /> Reading {importHost}…</> : <><Ic.Sparkle size={14} /> Import brand</>}
          </Button>
        </div>
        {importError && (
          <p className="card__hint" style={{ margin: "10px 0 0", color: "var(--red)" }}>
            <Ic.Bell size={12} /> {importError}
          </p>
        )}
        {evidence && (
          <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
            {evidence.title && <span className="card__hint">Harvested from “{evidence.title}”</span>}
            {(evidence.cssColors?.length || evidence.themeColor) && (
              <Field label="Harvested colors" hint="Click a swatch to use it as the accent.">
                <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  {evidence.themeColor && isHexColor(evidence.themeColor) && (
                    <Swatch
                      color={evidence.themeColor}
                      label={`${evidence.themeColor} · theme`}
                      selected={draft.accent === evidence.themeColor}
                      onClick={() => patch({ accent: evidence.themeColor })}
                    />
                  )}
                  {(evidence.cssColors ?? []).slice(0, 8).filter(([hex]) => isHexColor(hex)).map(([hex, count]) => (
                    <Swatch
                      key={hex}
                      color={hex}
                      label={`${hex} ×${count}`}
                      selected={draft.accent === hex}
                      onClick={() => patch({ accent: hex })}
                    />
                  ))}
                </span>
              </Field>
            )}
            {!!evidence.fonts?.length && (
              <Field label="Harvested fonts" hint="Click to use as the display face.">
                <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  {evidence.fonts.map((f) => (
                    <Chip
                      key={f}
                      tone="blue"
                      selected={draft.displayFont?.startsWith(`"${f}"`)}
                      onClick={() => patch({ displayFont: `"${f}", "Playfair Display", Georgia, serif` })}
                    >
                      {f}
                    </Chip>
                  ))}
                </span>
              </Field>
            )}
          </div>
        )}
        {logoCandidate && (
          <div className="row" style={{ gap: 12, marginTop: 14, alignItems: "center", flexWrap: "wrap" }}>
            <img
              className="brand-logo-preview brand-logo-preview--candidate"
              src={logoCandidate.logo}
              alt={logoCandidate.logoAlt || "Imported logo candidate"}
            />
            <span className="card__hint">Logo candidate from {importHost}</span>
            <Button variant="success" compact onClick={() => { patch({ logo: logoCandidate.logo, logoAlt: logoCandidate.logoAlt }); setLogoCandidate(null); }}>
              <Ic.Check size={13} /> Use logo
            </Button>
            <Button compact onClick={() => setLogoCandidate(null)}>Dismiss</Button>
          </div>
        )}
        {importWarnings.length > 0 && (
          <div className="row" style={{ gap: 6, marginTop: 12, flexWrap: "wrap" }}>
            {importWarnings.map((w, i) => <Chip key={i} tone="gold" icon={<Ic.Bell size={11} />}>{w}</Chip>)}
          </div>
        )}
      </Card>

      {/* palette + identity */}
      <Card
        className="setcard"
        title="Brand palette"
        hint="Empty = the default Mari look. Deep and ink shades derive automatically."
      >
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
          {COLOR_FIELDS.map(({ key, label, hint }) => (
            <Field key={key} label={label} hint={hint}>
              <span className="row" style={{ gap: 8, alignItems: "center" }}>
                <Input
                  type="color"
                  style={{ width: 52, padding: 3, height: 34 }}
                  value={effectiveColor(draft, key)}
                  onChange={(e) => patch({ [key]: e.target.value })}
                  aria-label={`${label} color`}
                />
                <code className="mono" style={{ fontSize: 12 }}>{effectiveColor(draft, key)}</code>
                {isHexColor(draft[key]) && (
                  <Button variant="link" compact onClick={() => patch({ [key]: undefined })}>auto</Button>
                )}
              </span>
            </Field>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14, marginTop: 14 }}>
          <Field label="Display font" hint="Family for headings (--display). Empty = Playfair Display.">
            <Input
              placeholder={'"Playfair Display", Georgia, serif'}
              value={draft.displayFont ?? ""}
              onChange={(e) => patch({ displayFont: e.target.value || undefined })}
            />
          </Field>
          <Field label="Body font" hint="Family for body prose (--serif). Empty = Lora.">
            <Input
              placeholder='"Lora", Georgia, serif'
              value={draft.bodyFont ?? ""}
              onChange={(e) => patch({ bodyFont: e.target.value || undefined })}
            />
          </Field>
          <Field
            label="Font stylesheet URL"
            hint="https link on fonts.googleapis.com or fonts.bunny.net — anything else is ignored."
          >
            <Input
              type="url"
              className="mono"
              placeholder="https://fonts.googleapis.com/css2?family=…"
              value={draft.fontUrl ?? ""}
              onChange={(e) => patch({ fontUrl: e.target.value || undefined })}
            />
          </Field>
        </div>
        {draft.fontUrl && !isAllowedFontUrl(draft.fontUrl) && (
          <p className="card__hint" style={{ margin: "6px 0 0", color: "var(--red)" }}>
            Only https URLs on fonts.googleapis.com or fonts.bunny.net are loaded — this one will be ignored.
          </p>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14, marginTop: 14 }}>
          <Field label="Logo" hint="PNG/SVG/JPG up to 200KB — shown in the sidebar instead of the Mari mark.">
            <Input type="file" accept="image/*" onChange={(e) => onLogoFile(e.target.files?.[0])} />
          </Field>
          <Field label="Logo alt text" hint="Read by screen readers in the sidebar.">
            <Input
              placeholder="Acme Inc. logo"
              value={draft.logoAlt ?? ""}
              onChange={(e) => patch({ logoAlt: e.target.value || undefined })}
            />
          </Field>
        </div>
        {draft.logo && (
          <div className="row" style={{ gap: 12, marginTop: 12, alignItems: "center" }}>
            <img
              className="brand-logo-preview"
              src={draft.logo}
              alt={draft.logoAlt || "Workspace logo preview"}
            />
            <span className="card__hint">{Math.round((draft.logo.length * 3) / 4 / 1024)}KB · shown at 74×60 in the sidebar</span>
            <ConfirmButton compact confirmLabel="Remove logo?" onConfirm={() => patch({ logo: undefined, logoAlt: undefined })}>
              <Ic.Trash size={13} /> Remove
            </ConfirmButton>
          </div>
        )}
        <div className="row" style={{ gap: 10, marginTop: 18 }}>
          <Button variant="primary" onClick={doSave} disabled={saveState.saving || !dirty}>
            {saveState.saving ? "Saving…" : "Save branding"}
          </Button>
          <ConfirmButton confirmLabel="Reset to Mari defaults?" onConfirm={doReset}>
            <Ic.Refresh size={13} /> Reset to defaults
          </ConfirmButton>
          {saveState.saved && <SavedNote>Brand applied</SavedNote>}
        </div>
      </Card>

      {/* live preview — real primitives under the draft tokens */}
      <Card
        className="setcard"
        title="Live preview"
        hint="Real components under your draft tokens — applies everywhere on Save"
      >
        <div style={previewVars}>
          <PageHeader
            eyebrow="Preview"
            title="Knowledge, in your brand"
            description="The same primitives every page composes — re-inked by your tokens."
            actions={<Button variant="primary" compact><Ic.Plus size={14} /> Primary action</Button>}
          />
          <div className="row" style={{ gap: 10, flexWrap: "wrap", marginTop: 4 }}>
            <Button variant="primary" compact>Primary</Button>
            <Button compact>Default</Button>
            <Button variant="success" compact><Ic.Check size={13} /> Success</Button>
            <Button variant="link" compact>Link button</Button>
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            <StatusChip status="verified" />
            <StatusChip status="canonical" />
            <StatusChip status="stale" />
            <Chip tone="terra" dot>accent</Chip>
            <Chip tone="green" dot>green</Chip>
            <Chip tone="blue" dot>blue</Chip>
            <Chip tone="gold" dot>gold</Chip>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginTop: 14 }}>
            <Stat value={132} label="Documents indexed" sub="+8 this week" tone="green" />
            <Stat value="98%" label="Fact-check pass rate" sub="last 30 days" tone="blue" />
            <Stat value={7} label="Stale answers" sub="needs review" tone="gold" />
          </div>
        </div>
      </Card>
    </>
  );
}

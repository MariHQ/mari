// Bring-your-own-branding — the proof that tokens ARE the brand.
//
// A workspace-level `branding` setting (jsonb) re-points the CSS custom
// properties in styles.css :root; every primitive inherits the new palette
// and no page changes. Empty object (or no setting) = the default Mari look:
// the :root defaults are the fallback brand, so there is no flash of a
// "wrong" brand while the settings query is in flight.
//
// Contract (all fields optional):
//   { accent, accentDeep, accentInk, green, blue, gold,
//     logo (data URI, ≤ ~200KB), logoAlt,
//     displayFont (--display), bodyFont (--serif, the body prose face),
//     fontUrl (https stylesheet on fonts.googleapis.com / fonts.bunny.net) }

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { gql, useQuery } from "./api";

export type Branding = {
  accent?: string;
  accentDeep?: string;
  accentInk?: string;
  green?: string;
  blue?: string;
  gold?: string;
  logo?: string;
  logoAlt?: string;
  displayFont?: string;
  bodyFont?: string;
  fontUrl?: string;
};

/** Logo uploads are capped at ~200KB (data URI lives in a jsonb setting). */
export const LOGO_MAX_BYTES = 200 * 1024;

/* ——— color derivation ——— */

const HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

function hexToRgb(hex: string): [number, number, number] | null {
  const m = HEX_RE.exec(hex.trim());
  if (!m) return null;
  let h = m[1];
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const toHex = (rgb: number[]) =>
  "#" + rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");

/** Mix `hex` toward `other` by t (0..1). Falls back to `hex` if unparsable. */
function mixHex(hex: string, other: [number, number, number], t: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  return toHex(rgb.map((v, i) => v + (other[i] - v) * t));
}

/** Darken by mixing toward black. accentDeep default = darken(accent, ~12%). */
export const darken = (hex: string, amount: number) => mixHex(hex, [0, 0, 0], amount);

/** WCAG-ish relative luminance (0..1). */
function luminance(hex: string): number {
  const rgb = hexToRgb(hex);
  if (!rgb) return 0;
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Ink to sit ON the accent: dark ink on light accents, white on dark. */
export const inkFor = (hex: string) => (luminance(hex) > 0.42 ? "#10263b" : "#ffffff");

export const isHexColor = (v: unknown): v is string => typeof v === "string" && HEX_RE.test(v.trim());

/* ——— token mapping ———
   Wash tokens are emitted as color-mix() expressions referencing the base
   vars, so they resolve correctly wherever the map is applied — on :root for
   the saved brand, or inline on a scoped preview container for drafts. */

const wash = (v: string, pct: number, base = "var(--card)") =>
  `color-mix(in srgb, ${v} ${pct}%, ${base})`;

/** Branding → CSS custom-property overrides. Only overrides what is set. */
export function computeCssVars(b: Branding): Record<string, string> {
  const vars: Record<string, string> = {};
  if (isHexColor(b.accent)) {
    const accent = b.accent.trim();
    const deep = isHexColor(b.accentDeep) ? b.accentDeep.trim() : darken(accent, 0.12);
    vars["--terra"] = accent;
    vars["--terra-deep"] = deep;
    vars["--terra-deeper"] = darken(deep, 0.15);
    vars["--terra-ink"] = isHexColor(b.accentInk) ? b.accentInk.trim() : inkFor(accent);
    vars["--terra-wash"] = wash(accent, 14);
    vars["--terra-wash-line"] = wash(accent, 42);
    vars["--terra-wash-ink"] = wash(accent, 88, "var(--ink)");
    // The default sidebar texture is an opaque terracotta-tinted PNG; under a
    // custom accent it would mask the brand color, so drop it.
    vars["--terra-texture"] = "none";
  }
  if (isHexColor(b.green)) {
    const green = b.green.trim();
    vars["--green"] = green;
    vars["--green-deep"] = darken(green, 0.16);
    vars["--green-deeper"] = darken(green, 0.3);
    vars["--green-wash"] = wash(green, 15);
    vars["--green-wash-line"] = wash(green, 45);
    vars["--green-wash-ink"] = wash(green, 85, "var(--ink)");
  }
  if (isHexColor(b.blue)) {
    const blue = b.blue.trim();
    vars["--blue"] = blue;
    vars["--blue-soft"] = mixHex(blue, [255, 255, 255], 0.18);
    vars["--blue-wash"] = wash(blue, 12);
    vars["--blue-wash-line"] = wash(blue, 38);
  }
  if (isHexColor(b.gold)) {
    const gold = b.gold.trim();
    vars["--gold"] = gold;
    vars["--gold-wash"] = wash(gold, 16);
    vars["--gold-wash-line"] = wash(gold, 44);
    vars["--gold-ink"] = wash(gold, 76, "var(--ink)");
  }
  if (b.displayFont && b.displayFont.trim()) {
    vars["--display"] = withFontFallback(b.displayFont, '"Inter", "Helvetica Neue", Arial, sans-serif');
  }
  // bodyFont maps to --serif: body prose is set in var(--serif) (Inter), so
  // that is "the body family" here. --sans (labels/meta) stays Mari's.
  if (b.bodyFont && b.bodyFont.trim()) {
    vars["--serif"] = withFontFallback(b.bodyFont, '"Helvetica Neue", Arial, sans-serif');
  }
  return vars;
}

/** A bare family name gets quoted + a fallback stack; full stacks pass through. */
function withFontFallback(font: string, fallback: string): string {
  const f = font.trim();
  if (f.includes(",")) return f;
  const quoted = /^["']/.test(f) ? f : `"${f}"`;
  return `${quoted}, ${fallback}`;
}

/* ——— brand font stylesheet injection ———
   Only https stylesheets from known font CDNs are ever injected — a branding
   record must never be able to load an arbitrary stylesheet. */

const ALLOWED_FONT_HOSTS = new Set(["fonts.googleapis.com", "fonts.bunny.net"]);

export function isAllowedFontUrl(url: unknown): url is string {
  if (typeof url !== "string" || !url.trim()) return false;
  try {
    const u = new URL(url.trim());
    return u.protocol === "https:" && ALLOWED_FONT_HOSTS.has(u.hostname);
  } catch {
    return false;
  }
}

const FONT_LINK_ATTR = "data-brand-font";

const findFontLink = (href: string) =>
  Array.from(document.head.querySelectorAll(`link[${FONT_LINK_ATTR}]`)).find(
    (l) => l.getAttribute("href") === href,
  );

/** Inject a <link rel="stylesheet"> for an allow-listed font URL (deduped on
 *  href). Silently ignores anything else. Used for saved brands and for the
 *  Branding editor's draft preview (fonts are global; that's fine). */
export function injectFontStylesheet(url: string): void {
  if (!isAllowedFontUrl(url)) return;
  const href = url.trim();
  if (findFontLink(href)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.setAttribute(FONT_LINK_ATTR, "1");
  document.head.appendChild(link);
}

/* ——— runtime application ——— */

let appliedKeys: string[] = [];
let appliedFontHref: string | null = null;

/** Map a branding object onto :root (plus the brand font stylesheet link).
 *  Clears whatever the last call set, so removing a value falls back to the
 *  styles.css default. */
function applyBranding(b: Branding) {
  const root = document.documentElement.style;
  for (const k of appliedKeys) root.removeProperty(k);
  const vars = computeCssVars(b);
  for (const [k, v] of Object.entries(vars)) root.setProperty(k, v);
  appliedKeys = Object.keys(vars);

  // Brand font stylesheet: inject when set (allow-listed hosts only), remove
  // ours when the brand changes or resets.
  const nextHref = isAllowedFontUrl(b.fontUrl) ? b.fontUrl.trim() : null;
  if (appliedFontHref && appliedFontHref !== nextHref) {
    findFontLink(appliedFontHref)?.remove();
  }
  if (nextHref) injectFontStylesheet(nextHref);
  appliedFontHref = nextHref;
}

/* ——— provider ——— */

type BrandingCtxValue = {
  branding: Branding;
  loading: boolean;
  save: (next: Branding) => Promise<boolean>;
  reset: () => Promise<boolean>;
};

const BrandingCtx = createContext<BrandingCtxValue | null>(null);

// Named query so its cache entry never collides with the settings pages'
// `{ settings { key value } }` (same shape, different map).
const BRANDING_Q = `query Branding { settings { key value } }`;
const mapBranding = (d: any): Branding =>
  ((d.settings ?? []).find((s: any) => s.key === "branding")?.value as Branding) ?? {};

export function BrandingProvider({ children }: { children: ReactNode }) {
  const q = useQuery<Branding>(BRANDING_Q, { map: mapBranding });
  const [local, setLocal] = useState<Branding | null>(null);
  const branding = local ?? q.data ?? {};

  // Apply before paint as soon as data (or a local save) lands. Until then
  // the :root defaults render — the default Mari brand is the fallback.
  const key = JSON.stringify(branding);
  useLayoutEffect(() => {
    applyBranding(JSON.parse(key) as Branding);
  }, [key]);

  const save = useCallback(async (next: Branding) => {
    const ok = await gql(
      `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`,
      { key: "branding", value: next },
    );
    if (ok) {
      setLocal(next); // re-apply immediately, no reload
      q.refetch();
    }
    return !!ok;
  }, [q]);

  const reset = useCallback(() => save({}), [save]);

  const value = useMemo(
    () => ({ branding, loading: q.loading, save, reset }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, q.loading, save, reset],
  );
  return <BrandingCtx.Provider value={value}>{children}</BrandingCtx.Provider>;
}

/** { branding, save(next), reset() } — must be used under BrandingProvider. */
export function useBranding(): BrandingCtxValue {
  const ctx = useContext(BrandingCtx);
  if (!ctx) throw new Error("useBranding must be used within <BrandingProvider>");
  return ctx;
}

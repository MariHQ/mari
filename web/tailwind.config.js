import { LIB } from "./lib-path.mjs";

/* Brand tokens, copied from mari-design/components/tailwind.config.js.
   The library is plain TSX consuming these utility classes, so its source has
   to be in `content` — otherwise Tailwind purges every class only it uses and
   the console renders as unstyled boxes. */

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    `${LIB}/**/*.{ts,tsx}`,
    `!${LIB}/node_modules/**`,
    `!${LIB}/dist/**`,
  ],
  theme: {
    extend: {
      colors: {
        paper: "#FFFFFF",
        ink: "#10263B",
        flysch: "#F0F2F5",
        biscay: { DEFAULT: "#1C3F60", 2: "#1E6FA8" },
        espelette: "#B23A1E",
        moss: "#2C6E49",
        clay: "#A05E1C",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
        /* `term` and `display` are NOT in mari-design's tailwind.config.js,
           but 128 of its source files use `font-term` and the design canvas it
           publishes resolves it to JetBrains Mono — its committed config has
           drifted behind its built CSS. Without these two, every mono chrome
           label (table headers, chips, counts) and every page heading silently
           falls back to the body sans. Values are BRAND-STYLE-GUIDE.md §
           Typography. Drop them once the library's own config defines them. */
        term: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        display: ["Inter", "Helvetica Neue", "Arial", "sans-serif"],
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "skeleton-fade": { "0%,100%": { opacity: "0.55" }, "50%": { opacity: "0.85" } },
      },
      animation: {
        shimmer: "shimmer 1.6s ease-in-out infinite",
        "skeleton-fade": "skeleton-fade 1.8s ease-in-out infinite",
      },
    },
  },
};

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
// @ts-expect-error -- plain ESM helper, shared with tailwind.config.js.
import { LIB } from "./lib-path.mjs";

/* The submodule is source only: it has no node_modules, and it sits outside
   this package's tree, so Node's ancestor lookup from a library file never
   reaches web/node_modules. Its bare imports ("react", "lucide-react", the
   Radix packages) therefore resolve to nothing at bundle time. Pointing them
   at THIS app's copies is both what makes the library build and what
   guarantees a single React instance — two would throw "Invalid hook call"
   in every library component while the build stayed green. */
const HERE = dirname(fileURLToPath(import.meta.url));

/** Package ROOT, not its entry file: aliasing "react" straight to index.js
    makes "react/jsx-runtime" resolve to "…/index.js/jsx-runtime".
    Resolved by path rather than require.resolve, because a package with an
    `exports` map (every current Radix package) does not expose
    "./package.json" and would look uninstalled when it is not. */

const SHARED_DEPS = [
  "react", "react-dom", "lucide-react",
  "@radix-ui/react-accordion", "@radix-ui/react-checkbox", "@radix-ui/react-context-menu",
  "@radix-ui/react-dialog", "@radix-ui/react-dropdown-menu", "@radix-ui/react-popover",
  "@radix-ui/react-progress", "@radix-ui/react-radio-group", "@radix-ui/react-separator",
  "@radix-ui/react-switch", "@radix-ui/react-tabs", "@radix-ui/react-toast",
  "@radix-ui/react-toggle", "@radix-ui/react-toggle-group", "@radix-ui/react-tooltip",
];
/* Two entries per package so both `react` and `react/jsx-runtime` land in the
   app's copy. Regex aliases, because an exact-string alias would also swallow
   the subpath form. */
const sharedAliases = SHARED_DEPS.flatMap((name) => {
  const root = resolve(HERE, "node_modules", name);
  // Only a missing directory is skipped, and nothing else is caught: a broad
  // try/catch here silently disabled every alias when the code above it threw.
  if (!existsSync(root)) return [];
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [
    { find: new RegExp(`^${esc}$`), replacement: root },
    { find: new RegExp(`^${esc}/(.*)$`), replacement: `${root}/$1` },
  ];
});

const API = "http://localhost:8000";
/** Everything the FastAPI server owns; the rest is this SPA. Mirrors nginx.conf. */
const API_ROUTES = [
  "/graphql", "/chat", "/agent", "/healthz", "/sites",
  "/auth", "/bots", "/webhooks", "/onboard", "/connectors",
];

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [{ find: "@mari-design/components", replacement: LIB }, ...sharedAliases],
    // The library is consumed from outside this package's tree and carries its
    // own node_modules. Without dedupe a second React can come along with it,
    // and two Reacts means every hook in the library throws "Invalid hook
    // call" at runtime while the build stays green.
    dedupe: ["react", "react-dom"],
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(API_ROUTES.map((r) => [r, API])),
  },
});

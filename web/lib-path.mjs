/* Where `@mari-design/components` resolves to.
 *
 * The pinned git submodule at ../vendor/mari-design, and nothing else. This
 * briefly preferred a sibling working checkout of the mari-design repo so the
 * app could build against the pure-presenter refactor before it was pushed;
 * that pointer has since moved, and the fallback is removed deliberately. A
 * build that silently prefers whatever happens to be checked out next to the
 * repo is not reproducible: it succeeds on the machine that has the sibling
 * and fails in CI and in Docker, where only the submodule exists.
 *
 * vite.config.ts, tailwind.config.js and scripts/smoke.mjs all read this, so
 * the build, the Tailwind content globs and the smoke test can never disagree
 * about which copy of the library is in play. tsconfig.json cannot import it,
 * so its `paths` entries name the same location.
 *
 * Clone with --recurse-submodules, or run:
 *   git submodule update --init --recursive
 *
 * Developing the library and the app together:
 *   MARI_LIB=~/mari-design/components npm run dev
 */

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

/* MARI_LIB points the build at a working checkout of the library instead, for
   the case where you are changing the library and the app together. Explicit
   and opt-in: the old behaviour was to silently prefer a sibling directory if
   one happened to exist, which meant the build passed here and failed in CI.
   Nothing sets this in Docker or CI, so those always use the pinned commit. */
export const LIB = process.env.MARI_LIB
  ? resolve(process.env.MARI_LIB)
  : resolve(HERE, "../vendor/mari-design/components");

if (!existsSync(LIB)) {
  throw new Error(
    `The component library is missing at ${LIB}.\n` +
    "The vendor/mari-design submodule is not checked out. Run:\n" +
    "  git submodule update --init --recursive",
  );
}

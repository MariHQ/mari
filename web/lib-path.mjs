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
 */

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

/** Absolute path to the component library. */
export const LIB = resolve(HERE, "../vendor/mari-design/components");

if (!existsSync(LIB)) {
  throw new Error(
    `The component library is missing at ${LIB}.\n` +
    "The vendor/mari-design submodule is not checked out. Run:\n" +
    "  git submodule update --init --recursive",
  );
}

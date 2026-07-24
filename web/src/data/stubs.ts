/* ══════════════════════════════════════════════════════════════════════════
   STUB ADAPTER — the one page that will never have one.
   ══════════════════════════════════════════════════════════════════════════

   Every console page now has a real adapter in `src/data/<page>.ts` except the
   Lookbook, which is the design system exhibiting itself. Its content is a set
   of deliberately pathological strings for truncation testing — unbreakable
   tokens, mixed scripts, huge numbers. There is no backend source for them and
   there should not be one: a query returning them would be the library testing
   itself through the product's database.

   The stub hands the page `null` and no error. Nothing else routes here. */

import type { Adapter, PageData } from "./types";

/** The only page id still rendering the library's own content. */
export const STUBBED = ["lookbook"] as const;

const NOTHING: PageData<unknown> = { data: null, loading: false, error: null };

/** Marker so a reader of index.ts can see at a glance which routes are live. */
export const stub: Adapter = () => NOTHING;

/* Overview actions.
 *
 * One intent: change the window the dashboard counts over. It lands in the
 * route rather than in the page, so the adapter re-queries `overviewStats`
 * with the new bound and a narrowed dashboard is a link someone can send.
 */

import type { OverviewActions } from "@mari-design/components/pages/OverviewPage";
import { rangeHref } from "../range";
import type { ActionContext } from "./index";

const AREA_HREF: Record<string, string> = {
  knowledge: "/knowledge",
  facts: "/facts",
  workflows: "/workflows",
};

export function overviewActions({ navigate }: ActionContext): OverviewActions {
  return {
    setRange: (range) => navigate(rangeHref("/", range, new URLSearchParams(window.location.search))),
    connectSources: () => navigate("/welcome"),
    // The headline tiles name work (changes, facts to review, active
    // workflows); clicking one lands where that work is done.
    openArea: (area) => navigate(AREA_HREF[area] ?? "/"),
  };
}

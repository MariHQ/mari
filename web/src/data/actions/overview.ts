/* Overview actions.
 *
 * One intent: change the window the dashboard counts over. It lands in the
 * route rather than in the page, so the adapter re-queries `overviewStats`
 * with the new bound and a narrowed dashboard is a link someone can send.
 */

import type { OverviewActions } from "@mari-design/components/pages/OverviewPage";
import { rangeHref } from "../range";
import type { ActionContext } from "./index";

export function overviewActions({ navigate }: ActionContext): OverviewActions {
  return {
    setRange: (range) => navigate(rangeHref("/", range, new URLSearchParams(window.location.search))),
  };
}

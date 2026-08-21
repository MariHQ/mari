import type { TrajectoriesActions } from "@mari-design/components/pages/TrajectoriesPage";
import type { ActionContext } from "./index";

export function trajectoriesActions({ replace }: ActionContext): TrajectoriesActions {
  const route = (changes: { category?: string | null; offset?: number }) => {
    const params = new URLSearchParams(window.location.search);
    if ("category" in changes) {
      if (changes.category) params.set("category", changes.category);
      else params.delete("category");
      params.delete("offset");
    }
    if (changes.offset !== undefined && changes.offset > 0) params.set("offset", String(changes.offset));
    else if (changes.offset !== undefined) params.delete("offset");
    const query = params.toString();
    replace(query ? `/trajectories?${query}` : "/trajectories");
  };
  return {
    setCategory: (category) => route({ category }),
    setOffset: (offset) => route({ offset }),
  };
}

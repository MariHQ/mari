import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { TrajectoriesData, TrajectoryRow } from "@mari-design/components/pages/TrajectoriesPage";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const PAGE = 25;
const QUERY = `query Trajectories($limit: Int!, $offset: Int!, $category: String) {
  trajectories(limit: $limit, offset: $offset, category: $category) {
    id sessionId prompt status model layer1 layer2 category macroIntent phases
    stepCount failureCount reworkCount startedAt completedAt
    steps { ordinal tool actionFamily args summary ok }
  }
  trajectoryTotal(category: $category)
  trajectoryCategories
}`;

type Res = {
  trajectories: TrajectoryRow[];
  trajectoryTotal: number;
  trajectoryCategories: string[];
};

export const EMPTY: TrajectoriesData = {
  rows: [], total: 0, categories: [], category: null, offset: 0, limit: PAGE,
};

export function buildTrajectories(res: Res | null, category: string | null, offset: number): TrajectoriesData {
  return {
    rows: (res?.trajectories ?? []).slice(0, PAGE),
    total: res?.trajectoryTotal ?? 0,
    categories: res?.trajectoryCategories ?? [],
    category,
    offset,
    limit: PAGE,
  };
}

export function useTrajectories(): PageData<TrajectoriesData> {
  const [params] = useSearchParams();
  const category = params.get("category") || null;
  const rawOffset = Number(params.get("offset") || 0);
  const offset = Number.isInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const query = useQuery<Res>(QUERY, {
    variables: { limit: PAGE, offset, category },
    map: (data: Res) => data,
  });
  const data = useMemo(() => buildTrajectories(query.data, category, offset),
    [query.data, category, offset]);
  return {
    data,
    loading: query.loading,
    error: query.error ? (query.errorText ?? "Agent trajectories are temporarily unavailable.") : null,
  };
}

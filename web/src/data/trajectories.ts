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
    steps { ordinal tool actionFamily args summary ok disposition editedArgs }
    evidence { documentId title reason rank relevance note }
    promotedWorkflowId promotedWorkflowStatus promotedWorkflowCachePolicy
    promotedWorkflowName workflowRootTrajectoryId workflowObservationCount
    promotedWorkflowCacheState promotedWorkflowCacheRefreshedAt promotedWorkflowDependencyCount
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
  const mapped = (res?.trajectories ?? []).slice(0, PAGE).map((row) => ({
    ...row,
    steps: (row.steps ?? []).map((step) => ({
      ...step, disposition: step.disposition ?? "included", editedArgs: step.editedArgs ?? null,
    })),
    evidence: row.evidence ?? [],
    promotedWorkflowId: row.promotedWorkflowId ?? null,
    promotedWorkflowName: row.promotedWorkflowName ?? "",
    workflowRootTrajectoryId: row.workflowRootTrajectoryId ?? null,
    workflowObservationCount: row.workflowObservationCount ?? 1,
    promotedWorkflowStatus: row.promotedWorkflowStatus ?? "",
    promotedWorkflowCachePolicy: row.promotedWorkflowCachePolicy ?? "none",
    promotedWorkflowCacheState: row.promotedWorkflowCacheState ?? "disabled",
    promotedWorkflowCacheRefreshedAt: row.promotedWorkflowCacheRefreshedAt ?? "",
    promotedWorkflowDependencyCount: row.promotedWorkflowDependencyCount ?? 0,
  }));
  const grouped = new Map<string, TrajectoryRow>();
  for (const row of mapped) {
    const key = row.promotedWorkflowId ? `workflow:${row.promotedWorkflowId}` : `observation:${row.id}`;
    const current = grouped.get(key);
    if (!current) {
      grouped.set(key, { ...row, clusterObservations: [row] });
      continue;
    }
    const observations = [...(current.clusterObservations ?? [current]), row];
    if (row.id === row.workflowRootTrajectoryId) grouped.set(key, { ...row, clusterObservations: observations });
    else current.clusterObservations = observations;
  }
  return {
    rows: [...grouped.values()],
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
    error: query.error ? (query.errorText ?? "Workflows are temporarily unavailable.") : null,
  };
}

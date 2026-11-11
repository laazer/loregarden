import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { AreaView } from "../../api/referenceAreasApi";

export const AREAS_QUERY_KEY = ["reference-areas"] as const;

/** Every live project area; the server owns them (`survey_areas`). */
export function useAreas() {
  return useQuery({ queryKey: AREAS_QUERY_KEY, queryFn: () => api.referenceAreas() });
}

/** How an area reads in a chip: its name, with the workspace when it is not the one in view. */
export function areaChipText(label: string, areas: readonly AreaView[], workspace?: string): string {
  const area = areas.find((candidate) => candidate.label === label);
  if (!area) return label;
  return area.workspace_slug === workspace ? area.name : `${area.workspace_slug} · ${area.name}`;
}

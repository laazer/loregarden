import { request } from "./http";

/** A part of a workspace a reference can be read for (`survey_areas`). */
export interface AreaView {
  id: string;
  workspace_slug: string;
  slug: string;
  /** `<workspace>/<slug>`: how the area is named everywhere. */
  label: string;
  name: string;
  description: string;
  keywords: string[];
  /** External id of the ticket parentless findings for it go under; null for the workspace's findings home. */
  home_ticket: string | null;
  archived: boolean;
}

export interface AreaRequest {
  workspace_slug: string;
  slug: string;
  name: string;
  description: string;
  keywords: string[];
  home_ticket: string;
}

export interface AreaUpdateRequest {
  name?: string;
  description?: string;
  keywords?: string[];
  /** "" clears it. */
  home_ticket?: string;
  archived?: boolean;
}

/** An area a source serves, by label or id, and why. */
export interface EntryArea {
  area: string;
  reason: string;
}

export type AreaSuggestMode = "agent" | "keywords";

export interface AreaSuggestRequest {
  entries: { url: string; name: string; why: string }[];
  mode: AreaSuggestMode;
}

export interface AreaSuggestion {
  url: string;
  areas: EntryArea[];
}

export interface AreaSuggestResult {
  mode: AreaSuggestMode;
  suggestions: AreaSuggestion[];
  /** What the agent proposed that could not be used. */
  warnings: string[];
}

export const referenceAreasApi = {
  referenceAreas: (includeArchived = false) =>
    request<AreaView[]>(`/api/reference-areas?${new URLSearchParams({ include_archived: String(includeArchived) })}`),
  addReferenceArea: (body: AreaRequest) =>
    request<AreaView>("/api/reference-areas", { method: "POST", body: JSON.stringify(body) }),
  updateReferenceArea: (areaId: string, body: AreaUpdateRequest) =>
    request<AreaView>(`/api/reference-areas/${encodeURIComponent(areaId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  suggestReferenceAreas: (body: AreaSuggestRequest) =>
    request<AreaSuggestResult>("/api/reference-areas/suggest", { method: "POST", body: JSON.stringify(body) }),
};

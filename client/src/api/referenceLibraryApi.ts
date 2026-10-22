import { request } from "./http";

/** Mirrors `LibraryState` in `server/loregarden/models/domain/survey_enums.py`. */
export type LibraryState =
  | "candidate"
  | "awaiting_approval"
  | "queued"
  | "reading"
  | "surveyed"
  | "refused";

export interface LibraryRow {
  id: string;
  name: string;
  url: string;
  kind: string;
  state: LibraryState;
  licence_name: string;
  licence_use: string | null;
  workspaces: string[];
  question: string;
  passes: number;
  last_read_at: string | null;
  tickets: number;
  unresolved_refs: number;
  open_unknowns: number;
  approval_id: string | null;
  priority: number;
}

export interface LibraryList {
  workspace_slug: string;
  counts: Record<LibraryState, number>;
  sources: LibraryRow[];
}

export interface LibraryPass {
  ordinal: number;
  label: string;
  mode: string;
  status: string;
  question: string;
  outcome: string | null;
  outcome_note: string;
  stopped_at: string;
  refused_reason: string;
  finished_at: string | null;
  files_read: number;
  files_examined: number;
  carrier: string | null;
}

export interface LibraryTicket {
  external_id: string;
  title: string;
  state: string;
  workspace: string;
  actions: string[];
  passes: number[];
}

export interface LibraryLearning {
  id: string;
  title: string;
  /** The body's opening, to name a learning titled only by its ticket. */
  excerpt: string;
  ticket: string;
  workspace: string;
  discredited: boolean;
  updated_at: string;
}

export interface LibraryDetail {
  source: LibraryRow;
  licence_constraint: string;
  pinned_commit: string;
  refused_reason: string;
  queued_reason: string;
  notes: string;
  notes_truncated: boolean;
  workspaces: { slug: string; question: string; why: string }[];
  passes: LibraryPass[];
  tickets: LibraryTicket[];
  unresolved_refs: string[];
  findings: { id: string; status: string; line: string }[];
  open_unknowns: { id: string; question: string; authority_required: string }[];
  coverage: {
    mapped_commit: string;
    tree_files: number;
    read: number;
    partial: number;
    skipped: number;
    unopened: number;
  };
  learnings: {
    availability: "found" | "empty" | "unavailable";
    reason: string;
    items: LibraryLearning[];
  };
}

export const referenceLibraryApi = {
  referenceLibrary: (workspaceSlug: string) =>
    request<LibraryList>(
      `/api/reference-library?${new URLSearchParams({ workspace_slug: workspaceSlug })}`,
    ),
  referenceSource: (sourceId: string) =>
    request<LibraryDetail>(`/api/reference-library/${encodeURIComponent(sourceId)}`),
};

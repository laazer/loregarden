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
  /** The default branch's newest commit, as of `upstream_checked_at`. */
  upstream_committed_at: string | null;
  /** Commits that branch is ahead of the reviewed commit; null when unknown. */
  commits_since_review: number | null;
  upstream_checked_at: string | null;
  /** Why the last check could not answer; "" when it did. */
  upstream_check_error: string;
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
  /** What the pass recorded, as written; "" when it recorded through the tools. */
  record: string;
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

export interface LinkedInitiative {
  id: string;
  external_id: string;
  title: string;
  /** Memberships this link added (open tickets only). */
  members_added: number;
}

export interface LinkResult {
  initiative_id: string;
  initiative: string;
  added: string[];
  skipped: { ticket: string; reason: string }[];
  settled_left_out: number;
}

export interface UnlinkResult {
  initiative: string;
  removed: string[];
  kept: string[];
}

export interface LibraryDetail {
  source: LibraryRow;
  /** Initiatives following this source: its open tickets are their members. */
  initiatives: LinkedInitiative[];
  licence_constraint: string;
  pinned_commit: string;
  refused_reason: string;
  queued_reason: string;
  /** What the record says about the source, in full (markdown). */
  record: string;
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
  linkSourceInitiative: (sourceId: string, initiativeId: string) =>
    request<LinkResult>(`/api/reference-library/${encodeURIComponent(sourceId)}/initiatives`, {
      method: "POST",
      body: JSON.stringify({ initiative_id: initiativeId }),
    }),
  checkSourceUpstream: (sourceId: string) =>
    request<LibraryRow>(`/api/reference-library/${encodeURIComponent(sourceId)}/check-upstream`, {
      method: "POST",
      body: "{}",
    }),
  unlinkSourceInitiative: (sourceId: string, initiativeId: string) =>
    request<UnlinkResult>(
      `/api/reference-library/${encodeURIComponent(sourceId)}/initiatives/${encodeURIComponent(initiativeId)}`,
      { method: "DELETE" },
    ),
};

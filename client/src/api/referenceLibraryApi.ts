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
  /** What it is, in a line (GitHub's description or the page's opening); null until read. */
  description: string | null;
  language: string;
  stars: number | null;
  /** Comma-separated GitHub topics. */
  topics: string;
  /** Why the last read of the description failed; "" when it did not. */
  describe_error: string;
  /** The topic of the discovery run that found it, when an agent did. */
  discovered_by: string | null;
}

export type SurveyTopic =
  | "memory_layer"
  | "ui_ux"
  | "similar_tools"
  | "agent_orchestration"
  | "testing_verification";

export interface TopicView {
  id: SurveyTopic;
  label: string;
  /** Fills "What should the survey answer?". */
  question: string;
  /** Fills "Why this one?". */
  why: string;
  /** What a discovery run searches for. */
  search: string;
}

export interface DiscoveryRequest {
  topic: SurveyTopic;
  workspace_slugs: string[];
  initiative_ids: string[];
  focus: string;
  wanted: number;
}

export interface DiscoveryView {
  id: string;
  topic: SurveyTopic;
  label: string;
  focus: string;
  wanted: number;
  workspaces: string[];
  /** The run's ticket and its state; the run is over when the ticket is settled. */
  carrier: string | null;
  carrier_state: string | null;
  /** Sources it has proposed so far. */
  found: number;
  created_at: string;
}

export interface LibraryList {
  workspace_slug: string;
  counts: Record<LibraryState, number>;
  sources: LibraryRow[];
  /** What the survey reviews next and what holds it, across all workspaces. */
  schedule: QueueSchedule;
  /** Recent discovery runs serving this workspace, newest first. */
  discoveries: DiscoveryView[];
}

export interface QueueSchedule {
  /** False when this server runs no survey loop; `disabled_reason` says why. */
  enabled: boolean;
  disabled_reason: string;
  interval_seconds: number;
  last_tick_at: string | null;
  /** When the next check runs; null until this server's first check. */
  next_tick_at: string | null;
  max_concurrent: number;
  running: { source_id: string; name: string; ordinal: number; carrier: string | null }[];
  next: {
    source_id: string;
    name: string;
    ordinal: number;
    mode: string;
    /** Why it is not starting; "" when it starts at the next check. */
    waiting: string;
    /** Whether Review now would start it: nothing but the clock holds it. */
    can_start_now: boolean;
  } | null;
  /** Each queued source's place in line, 1 being next. */
  positions: Record<string, number>;
}

export type SourceKind = "repo" | "site" | "released_app";

export interface AddSourceRequest {
  workspace_slugs: string[];
  kind: SourceKind;
  url: string;
  question: string;
  queued_reason: string;
  /** Initiatives to follow the source: its open tickets join them, now and later. */
  initiative_ids: string[];
}

export interface AddSourceResult {
  source: LibraryRow;
  attached: string[];
  scoped_pass: number | null;
  /** Initiatives now following the source, by external id. */
  initiatives_linked: string[];
}

export interface ReviewNowRequest {
  /** What a new pass on a surveyed source answers; "" keeps the source's own. */
  question: string;
  /** Why a refused source should be read after all; required to reopen one. */
  reason?: string;
  /** Workspaces the pass should read for as well. */
  workspace_slugs: string[];
  initiative_ids: string[];
}

export interface LinkTargetsRequest {
  workspace_slugs: string[];
  initiative_ids: string[];
  /** What it should answer for the new workspaces; optional. */
  question: string;
  queued_reason: string;
}

export interface LinkTargetsResult {
  /** Workspaces this call made the source serve. */
  attached: string[];
  /** A scoped pass queued because a surveyed source gained a workspace. */
  scoped_pass: number | null;
  /** What each initiative link did. */
  initiatives: LinkResult[];
}

export interface BulkAddRequest {
  workspace_slugs: string[];
  urls: string[];
  /** One kind for every URL, or null to tell each URL's kind from its host. */
  kind: SourceKind | null;
  question: string;
  queued_reason: string;
  initiative_ids: string[];
  /** Per-URL name and reason, each naming one of `urls`; a URL with none takes the shared reason. */
  entries?: BulkAddEntry[];
}

export interface BulkAddEntry {
  url: string;
  name: string;
  queued_reason: string;
}

export interface BulkAddItem {
  url: string;
  outcome: "queued" | "proposed" | "attached" | "rescoped" | "already" | "failed";
  kind: SourceKind;
  source: LibraryRow | null;
  /** Why it failed; "" otherwise. */
  detail: string;
  initiatives_linked: string[];
}

export interface ReviewNowResult {
  started: boolean;
  ordinal: number;
  carrier: string | null;
  /** Why it did not start; it stays first in line. */
  waiting: string;
  /** Set when the dispatch closed the pass instead (licence, nothing unread). */
  refused: string;
  workspaces_added: string[];
  initiatives_linked: string[];
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
  addReferenceSource: (body: AddSourceRequest) =>
    request<AddSourceResult>("/api/reference-library/sources", { method: "POST", body: JSON.stringify(body) }),
  referenceTopics: () => request<TopicView[]>("/api/reference-library/topics"),
  startDiscovery: (body: DiscoveryRequest) =>
    request<DiscoveryView>("/api/reference-library/discover", { method: "POST", body: JSON.stringify(body) }),
  keepSource: (sourceId: string) =>
    request<LibraryRow>(`/api/reference-library/${encodeURIComponent(sourceId)}/keep`, { method: "POST", body: "{}" }),
  dropSource: (sourceId: string, reason: string) =>
    request<LibraryRow>(`/api/reference-library/${encodeURIComponent(sourceId)}/drop`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  addReferenceSources: (body: BulkAddRequest) =>
    request<BulkAddItem[]>("/api/reference-library/sources/bulk", { method: "POST", body: JSON.stringify(body) }),
  reviewSourceNow: (sourceId: string, body: ReviewNowRequest) =>
    request<ReviewNowResult>(`/api/reference-library/${encodeURIComponent(sourceId)}/review-now`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  linkSourceTargets: (sourceId: string, body: LinkTargetsRequest) =>
    request<LinkTargetsResult>(`/api/reference-library/${encodeURIComponent(sourceId)}/links`, {
      method: "POST",
      body: JSON.stringify(body),
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

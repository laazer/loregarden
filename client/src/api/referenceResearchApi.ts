import { request } from "./http";
import type { BulkAddItem } from "./referenceLibraryApi";

/** How far a capability claim was checked; only `verified` names a file at a commit. */
export type ClaimStatus = "verified" | "docs_only" | "not_found" | "refuted";

export interface ResearchClaim {
  id: string;
  capability: string;
  status: ClaimStatus;
  evidence_path: string;
  evidence_commit: string;
  /** The cited lines (1-based, inclusive); 0 when the claim cites none. */
  evidence_start: number;
  evidence_end: number;
  /** Those lines as the file held them at the commit, read by the server. */
  evidence_excerpt: string;
  evidence_url: string;
  note: string;
  recorded_by: "agent" | "operator";
  created_at: string;
}

export interface ResearchCandidate {
  id: string;
  url: string;
  name: string;
  summary: string;
  /** The library source for this URL, when it is in the library. */
  source_id: string | null;
  licence_name: string;
  language: string;
  stars: number | null;
  last_commit_at: string | null;
  /** Why licence, stars and last commit are missing, when they are. */
  metadata_error: string;
  /** First put forward in the question's latest run, after an earlier one. */
  new: boolean;
  /** Capability -> the claim that stands, or null when nothing was claimed. */
  claims: Record<string, ResearchClaim | null>;
}

export interface ResearchQuestion {
  id: string;
  thread_id: string;
  question: string;
  capabilities: string[];
  run_count: number;
  last_run_at: string | null;
  saved: boolean;
  /** Days between automatic re-runs; null re-runs only when asked. */
  interval_days: number | null;
  area_id: string | null;
  /** Why the last scheduled re-run could not start; "" when it did. */
  schedule_error: string;
  candidates: ResearchCandidate[];
}

/** One project and one capability: the claim that stands, for the claim card. */
export interface ResearchClaimCard {
  url: string;
  capability: string;
  /** The name research put the project forward under, or "" if never a candidate. */
  name: string;
  claim: ResearchClaim | null;
  /** Other claims recorded about this project and capability, replaced or outranked. */
  earlier: number;
}

export interface ResearchQuestionRef {
  id: string;
  question: string;
  thread_id: string;
}

/** Everything research knows about one project, across every question. */
export interface ResearchProject {
  url: string;
  name: string;
  /** The library source for this URL, when it is in the library. */
  source_id: string | null;
  licence_name: string;
  language: string;
  stars: number | null;
  last_commit_at: string | null;
  /** The claim that stands per capability, newest first; a person's refutation wins. */
  claims: ResearchClaim[];
  /** The questions that put it forward, newest first. */
  questions: ResearchQuestionRef[];
}

/** One research question in a workspace, for the library's list of them. */
export interface ResearchQuestionSummary {
  id: string;
  question: string;
  thread_id: string;
  capabilities: string[];
  run_count: number;
  last_run_at: string | null;
  candidates: number;
  /** Saved questions list first and can re-run on their own. */
  saved: boolean;
  interval_days: number | null;
  /** The project area it is pinned to, by name; "" when none. */
  area_name: string;
  schedule_error: string;
}

export interface SaveQuestionRequest {
  /** False un-saves: it leaves the library's list and stops re-running. */
  saved: boolean;
  area_id: string | null;
  interval_days: number | null;
}

export interface RerunStarted {
  question_id: string;
  thread_id: string;
  /** The pending assistant turn the re-run is answered in. */
  turn_id: string;
}

export interface ResearchClaimChange {
  url: string;
  name: string;
  capability: string;
  /** The claim before this run; null when this run is the first to check it. */
  before: ResearchClaim | null;
  after: ResearchClaim;
}

/** What one run of a research question changed against the run before it. */
export interface ResearchRunDiff {
  question_id: string;
  question: string;
  run: number;
  /** 0 for a question's first run. */
  previous_run: number;
  /** Only the latest run can say which projects dropped out. */
  dropped_known: boolean;
  added: ResearchCandidate[];
  dropped: ResearchCandidate[];
  changed: ResearchClaimChange[];
  /** Claims this run re-checked and found as they were. */
  unchanged: number;
}

export interface QueuePicksRequest {
  urls: string[];
  /** The question they answer; its text becomes the survey pass's question. */
  question_id?: string | null;
  /** Required without a question. */
  workspace_slug?: string;
  /** Project area labels every pick serves. */
  areas?: string[];
}

export interface RefuteClaimRequest {
  url: string;
  capability: string;
  note: string;
  question_id?: string | null;
}

export const referenceResearchApi = {
  researchQuestion: (questionId: string) =>
    request<ResearchQuestion>(`/api/reference-research/questions/${encodeURIComponent(questionId)}`),
  researchClaim: (url: string, capability: string) =>
    request<ResearchClaimCard>(
      `/api/reference-research/claims?${new URLSearchParams({ url, capability }).toString()}`,
    ),
  researchProject: (url: string) =>
    request<ResearchProject>(`/api/reference-research/projects?${new URLSearchParams({ url }).toString()}`),
  researchQuestions: (workspace: string) =>
    request<ResearchQuestionSummary[]>(
      `/api/reference-research/questions?${new URLSearchParams({ workspace }).toString()}`,
    ),
  /** Queue picks for a survey pass, admitted, each linked to the question it answers. */
  queueResearchPicks: (body: QueuePicksRequest) =>
    request<BulkAddItem[]>("/api/reference-research/queue", { method: "POST", body: JSON.stringify(body) }),
  /** Save, re-pin or un-save a question; an interval makes it re-run on its own. */
  saveResearchQuestion: (questionId: string, body: SaveQuestionRequest) =>
    request<ResearchQuestion>(`/api/reference-research/questions/${encodeURIComponent(questionId)}/save`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Re-run a question now, as a turn in its thread; 409 while that thread is busy. */
  rerunResearchQuestion: (questionId: string) =>
    request<RerunStarted>(`/api/reference-research/questions/${encodeURIComponent(questionId)}/rerun`, {
      method: "POST",
    }),
  researchRunDiff: (questionId: string, run?: number | null) =>
    request<ResearchRunDiff>(
      `/api/reference-research/questions/${encodeURIComponent(questionId)}/diff${run ? `?run=${run}` : ""}`,
    ),
  /** A person's correction: this project does not do this. It stands over agent claims. */
  refuteResearchClaim: (body: RefuteClaimRequest) =>
    request<ResearchClaim>("/api/reference-research/claims/refute", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

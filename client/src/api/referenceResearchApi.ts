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
  /** A person's correction: this project does not do this. It stands over agent claims. */
  refuteResearchClaim: (body: RefuteClaimRequest) =>
    request<ResearchClaim>("/api/reference-research/claims/refute", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

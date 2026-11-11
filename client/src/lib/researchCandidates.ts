/**
 * Sorting and labelling a research question's candidates, kept out of the table
 * component so the rules can be tested on their own.
 */

import type { ClaimStatus, ResearchCandidate, ResearchClaim } from "../api/referenceResearchApi";

/** Query keys for research reads; a refutation invalidates both roots. */
export const RESEARCH_QUESTION_ROOT = "research-question";
export const RESEARCH_CLAIM_ROOT = "research-claim";
export const RESEARCH_PROJECT_ROOT = "research-project";
export const researchProjectKey = (url: string) => [RESEARCH_PROJECT_ROOT, url] as const;
export const researchQuestionsKey = (workspace: string) => ["research-questions", workspace] as const;
export const researchQuestionKey = (questionId: string) => [RESEARCH_QUESTION_ROOT, questionId] as const;
export const researchClaimKey = (url: string, capability: string) =>
  [RESEARCH_CLAIM_ROOT, url, capability] as const;

export const CLAIM_LABELS: Record<ClaimStatus, string> = {
  verified: "Verified",
  docs_only: "Docs only",
  not_found: "Not found",
  refuted: "Refuted",
};

/** Better evidence sorts first: verified, then docs, then looked-for-and-absent. */
const CLAIM_RANK: Record<ClaimStatus, number> = {
  verified: 3,
  docs_only: 2,
  not_found: 1,
  refuted: 0,
};

/** `capability:<name>` sorts by that capability's claim. */
export type CandidateSortKey = "name" | "stars" | "last_commit" | `capability:${string}`;
export interface CandidateSort {
  key: CandidateSortKey;
  dir: "ascending" | "descending";
}

function claimRank(claim: ResearchClaim | null | undefined): number {
  return claim ? CLAIM_RANK[claim.status] : -1;
}

function sortValue(candidate: ResearchCandidate, key: CandidateSortKey): number | string {
  if (key === "name") return candidate.name.toLowerCase();
  if (key === "stars") return candidate.stars ?? -1;
  if (key === "last_commit") return candidate.last_commit_at ? Date.parse(candidate.last_commit_at) : -1;
  return claimRank(candidate.claims[key.slice("capability:".length)]);
}

export function sortCandidates(rows: ResearchCandidate[], sort: CandidateSort): ResearchCandidate[] {
  const sign = sort.dir === "ascending" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, sort.key);
    const right = sortValue(b, sort.key);
    if (left < right) return -sign;
    if (left > right) return sign;
    return a.name.localeCompare(b.name);
  });
}

/** Names default to A–Z; everything else to best-first. */
export function nextSort(current: CandidateSort, key: CandidateSortKey): CandidateSort {
  if (current.key === key) {
    return { key, dir: current.dir === "ascending" ? "descending" : "ascending" };
  }
  return { key, dir: key === "name" ? "ascending" : "descending" };
}

/** What a claim's badge says on hover: the note, and the file and commit behind it. */
export function claimEvidence(claim: ResearchClaim): string {
  const where = claim.evidence_path
    ? `${claim.evidence_path} @ ${claim.evidence_commit.slice(0, 12)}`
    : claim.evidence_url;
  const who = claim.recorded_by === "operator" ? "A person said so" : "";
  return [claim.note, where, who].filter(Boolean).join(" · ");
}

/** How many candidates have every capability verified: the card's one-line summary. */
export function fullyVerified(rows: ResearchCandidate[], capabilities: string[]): number {
  return rows.filter((row) => capabilities.every((cap) => row.claims[cap]?.status === "verified")).length;
}

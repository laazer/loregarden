import type { MergeQueueEntry, MergeQueueState, MergeQueueWait } from "../../api/mergeQueueApi";

/** The loop advances on the CI poll tick (120s); reading faster shows nothing new. */
export const QUEUE_REFETCH_MS = 30_000;

export const STATE_LABELS: Record<MergeQueueState, string> = {
  queued: "Queued",
  staging: "Staging",
  testing: "Testing",
  merging: "Merging",
  merged: "Merged",
  failed: "Failed",
  timed_out: "Timed out",
  withdrawn: "Withdrawn",
};

/** Why a live entry is not moving, in the operator's words. */
export const WAIT_LABELS: Record<MergeQueueWait, string> = {
  "": "",
  behind: "behind the head",
  checks_unreported: "waiting for required checks to start",
  checks_running: "waiting for required checks to finish",
  github_unreachable: "GitHub did not answer; retrying",
  base_moved: "the base moved; staging again",
  conflict_artifact_open: "conflicts with the base; resolve the bug ticket",
};

export function isStopped(state: MergeQueueState): boolean {
  return state === "failed" || state === "timed_out" || state === "withdrawn";
}

export function isFinished(state: MergeQueueState): boolean {
  return state === "merged" || isStopped(state);
}

/** An entry's name: its ticket's title, else (a PR admitted by number) its branch. */
export function entryName(entry: MergeQueueEntry): string {
  return entry.ticket_title || entry.source_branch || `PR #${entry.pr_number}`;
}

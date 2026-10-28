import type { StageStatus } from "../api/types";

const STAGE_STATUSES: StageStatus[] = [
  "pending",
  "running",
  "blocked",
  "awaiting",
  "done",
  "wont_do",
];

/**
 * The statuses an operator may pick for a stage now saved as `saved`.
 * `awaiting` is reached by raising an approval, never by naming it: the server
 * refuses it where none is pending, so it is offered only to keep a stage that
 * already awaits one.
 */
export function stageStatusChoices(saved: StageStatus | null): StageStatus[] {
  return STAGE_STATUSES.filter((s) => s !== "awaiting" || saved === "awaiting");
}

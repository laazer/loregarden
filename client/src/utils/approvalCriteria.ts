import type { Approval } from "../api/client";

/**
 * Whether an approval asks a human to verify something, rather than to wave a
 * tool call through.
 *
 * A stage sign-off carries the acceptance criteria the human reads before
 * approving; a testing checklist is the same ask arriving on any kind. Both
 * deserve the full-width Approvals tab — a permission prompt does not.
 */
export function hasHumanCriteria(approval: Approval): boolean {
  return approval.kind === "workflow_gate" || Boolean(approval.checklist?.length);
}

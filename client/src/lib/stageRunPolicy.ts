/**
 * Whether a stage can be started right now, and what the button should say.
 *
 * Pure policy, and the reason string is the whole point: every refusal names
 * the thing to resolve first, so a disabled button explains itself instead of
 * looking broken.
 */

import type { TicketDetail, WorkflowStageView } from "../api/types";
import { STATE_LABELS } from "../components/UpdateStateModal";

export interface StageRunCheck {
  allowed: boolean;
  reason: string;
}

export function canRunStage(ticket: TicketDetail, stage: WorkflowStageView): StageRunCheck {
  if (stage.key === "done") {
    if (ticket.state === "done") {
      return { allowed: false, reason: "Ticket already complete" };
    }
    if (stage.status === "done") {
      return { allowed: false, reason: "Ticket already complete" };
    }
  }
  if (stage.status === "wont_do") {
    return { allowed: false, reason: "Stage marked won't do" };
  }
  if (ticket.state === "done" || ticket.state === "wont_do") {
    return { allowed: false, reason: `Ticket is ${STATE_LABELS[ticket.state]}` };
  }
  if (ticket.workflow_stage_status === "awaiting") {
    return { allowed: false, reason: "Resolve approval before running another stage" };
  }
  if (ticket.workflow_stage_status === "running" && stage.key !== ticket.workflow_stage_key) {
    return { allowed: false, reason: "Current stage is still running" };
  }
  if (ticket.state === "blocked") {
    const retryable =
      stage.status === "blocked" ||
      stage.status === "done" ||
      (stage.key === ticket.workflow_stage_key &&
        (ticket.workflow_stage_status === "blocked" || ticket.workflow_stage_status === "running"));
    if (!retryable) {
      return { allowed: false, reason: "Resolve the blocked stage before running another" };
    }
  }
  const verb =
    stage.key === "done"
      ? "Complete"
      : stage.status === "done" || stage.status === "blocked"
        ? "Re-run"
        : "Run";
  return { allowed: true, reason: `${verb} ${stage.name}` };
}

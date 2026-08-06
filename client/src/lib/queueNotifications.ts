/**
 * Map a forwarded queue event into toast + inbox copy.
 *
 * The snapshot cannot say that a run just finished; these events can. Labels
 * come from the server when present so the operator sees the ticket and stage
 * rather than a truncated run id.
 */

import type { QueueEvent } from "./queueSocket";
import { pushInboxNotification } from "../state/notificationStore";
import { pushToast, type ToastInput } from "../state/toastStore";

function ticketLabel(data: QueueEvent["data"]): string {
  if (!data) return "a run";
  if (data.ticketTitle?.trim()) return data.ticketTitle.trim();
  if (data.ticketId) return data.ticketId.slice(0, 8);
  if (data.runId) return data.runId.slice(0, 8);
  return "a run";
}

/** Workflow stage, falling back to the agent that ran. */
function stepLabel(data: QueueEvent["data"]): string | null {
  if (!data) return null;
  if (data.stageKey?.trim()) return data.stageKey.trim();
  if (data.agentId?.trim()) return data.agentId.trim();
  return null;
}

function runSubject(data: QueueEvent["data"]): string {
  const ticket = ticketLabel(data);
  const step = stepLabel(data);
  return step ? `${ticket} · ${step}` : ticket;
}

/** A forwarded queue event, as a toast for the app-wide stack. */
export function queueEventToToast(event: QueueEvent): ToastInput {
  if (event.type === "queue_promoted") {
    return {
      tone: "info",
      title: "Run promoted",
      message: `${runSubject(event.data)} took slot ${event.data?.slotNumber ?? "?"}.`,
    };
  }

  if (event.type === "run_completed") {
    const failed = event.data?.status !== "succeeded";
    const subject = runSubject(event.data);
    return {
      tone: failed ? "error" : "success",
      title: failed ? "Run failed" : "Run complete",
      message: `${subject} finished as ${event.data?.status ?? "unknown"}.`,
    };
  }

  return {
    tone: "error",
    title: "Queue error",
    message: event.data?.message ?? "The queue reported a failure.",
    // Stays until dismissed: a queue failure is the one thing worth reading late.
    duration: 0,
  };
}

/** Toast + durable inbox entry for one queue event. */
export function announceQueueEvent(event: QueueEvent): void {
  const toast = queueEventToToast(event);
  pushToast(toast);
  pushInboxNotification({
    tone: toast.tone,
    title: toast.title,
    message: toast.message,
    ticketId: event.data?.ticketId,
    runId: event.data?.runId,
    createdAt: event.timestamp,
  });
}

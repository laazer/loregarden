import type { Query } from "@tanstack/react-query";

import { ApiError } from "../../../api/client";
import type { TicketDetail } from "../../../api/types";
import { ticketIsRunning } from "./useRunControls";

/** Invented / deleted ticket ids must not keep hammering the API. */
export function isTicketNotFound(error: unknown): boolean {
  if (error instanceof ApiError && error.status === 404) return true;
  if (error instanceof Error && /ticket not found/i.test(error.message)) return true;
  return false;
}

export function ticketQueryRetry(failureCount: number, error: unknown): boolean {
  if (isTicketNotFound(error)) return false;
  return failureCount < 2;
}

/**
 * Poll live tickets while they exist; stop cold when the lookup failed.
 *
 * Without this, a faked `ticket` card (missing id) refetches every 5s forever.
 */
export function ticketRefetchInterval(
  query: Query<TicketDetail, Error, TicketDetail, readonly unknown[]>,
): number | false {
  if (query.state.error) return false;
  const data = query.state.data;
  if (!data) return false;
  return ticketIsRunning(data.workflow_stage_status) ? 1000 : 5000;
}

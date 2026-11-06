/**
 * How the ticket page words what a landing is waiting on (TicketLandingPanel).
 */

import type { RefusedAttempt } from "../api/ticketLandingApi";

/** Why the last attempt was refused, in the words that say whose problem it is:
 * a failing suite is the ticket's, tests that never ran are the machine's. */
export function refusalReason(attempt: RefusedAttempt): string {
  if (attempt.outcome === "failed") {
    return attempt.exit_code === null ? "the tests failed" : `the tests failed (exit ${attempt.exit_code})`;
  }
  return attempt.exit_code === null
    ? "the tests never ran (they timed out or could not start)"
    : "the tests reported they could not run";
}

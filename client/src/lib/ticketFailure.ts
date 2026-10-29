/**
 * What stopped a ticket, read once for the ticket page: the block or error
 * the ticket reports, the stage visit and attempt it came from, and the
 * attempts before it folded into runs that failed the same way.
 *
 * The failure leads the page, so the operator reads the error before any
 * history; the Timeline then points back at it instead of repeating it.
 */

import type { TicketDetail } from "../api/client";
import type { LedgerAttempt, RunErrorArtifact } from "../api/types";
import { isFailedStatus, type TimelineVisit } from "./ticketTimeline";

export interface TicketFailure {
  /** The ticket says it is blocked, rather than only that a run failed. */
  blocked: boolean;
  /** The ticket's own words for what stopped it; "" when none were recorded. */
  message: string;
  error: RunErrorArtifact | null;
  /** The visit and attempt the failure came from, when the ledger has them. */
  visit: TimelineVisit | null;
  attempt: LedgerAttempt | null;
  /** 1-based, among the visit's attempts. */
  attemptNumber: number;
}

/**
 * The ticket's failure, or null when nothing has stopped it. "Blocked" is the
 * ticket's word for itself, never inferred from an artifact: a failed run the
 * orchestrator is about to retry is not a block.
 */
export function ticketFailure(ticket: TicketDetail, visits: readonly TimelineVisit[]): TicketFailure | null {
  // The server sends only an error nothing has run since; older ones are
  // outputs on the visits, not a state the ticket is in.
  const error = ticket.artifacts?.error ?? null;
  const blocked = ticket.state === "blocked" || Boolean(ticket.blocking_issues?.trim());
  // `blocking_issues` is only a pointer ("see the Timeline tab") when the message
  // was long; `block_message` is the text it points at.
  const blockText = (ticket.block_message || ticket.blocking_issues)?.trim() ?? "";
  const message = (blocked ? blockText : "") || error?.message?.trim() || "";
  if (!blocked && !message) return null;

  const found = failedAttempt(visits, error);
  return {
    blocked,
    message,
    error,
    visit: found?.visit ?? null,
    attempt: found?.attempt ?? null,
    attemptNumber: found ? found.visit.visit.attempts.indexOf(found.attempt) + 1 : 0,
  };
}

/**
 * The attempt behind the failure: the error's own run when the ledger has it,
 * else the newest failed attempt of the newest visit that has not recovered.
 */
function failedAttempt(
  visits: readonly TimelineVisit[],
  error: RunErrorArtifact | null,
): { visit: TimelineVisit; attempt: LedgerAttempt } | null {
  const newestFirst = [...visits].reverse();
  if (error?.run_code) {
    for (const visit of newestFirst) {
      const attempt = visit.visit.attempts.find((a) => a.run_code === error.run_code);
      if (attempt) return { visit, attempt };
    }
  }
  const visit = newestFirst.find(
    (v) => v.failedAttempts.length > 0 && !v.latestRunSucceeded && (!error?.stage_key || v.visit.stage_key === error.stage_key),
  );
  if (!visit) return null;
  return { visit, attempt: visit.failedAttempts[visit.failedAttempts.length - 1] };
}

/** One row of a visit's attempts: an attempt, or a run of attempts that failed back to back. */
export type AttemptRow =
  | { kind: "attempt"; attempt: LedgerAttempt; number: number }
  | {
      kind: "failed-run";
      attempts: LedgerAttempt[];
      first: number;
      last: number;
      /** Every attempt in the run recorded the same, non-empty error. */
      sameError: boolean;
    };

/**
 * A visit's attempts, oldest first, with each stretch of two or more
 * consecutive failures folded into one row. `standAlone` (the attempt the
 * page already shows above) is never folded. Parallel lanes are not retries,
 * so a fan-out is never folded.
 */
export function attemptRows(
  attempts: readonly LedgerAttempt[],
  { isParallel, standAlone }: { isParallel: boolean; standAlone: string | null },
): AttemptRow[] {
  const rows: AttemptRow[] = [];
  let pending: { attempt: LedgerAttempt; number: number }[] = [];
  const flush = () => {
    if (pending.length >= 2) {
      const tails = pending.map(({ attempt }) => attempt.error_tail);
      rows.push({
        kind: "failed-run",
        attempts: pending.map(({ attempt }) => attempt),
        first: pending[0].number,
        last: pending[pending.length - 1].number,
        sameError: tails[0] !== "" && tails.every((tail) => tail === tails[0]),
      });
    } else {
      rows.push(...pending.map(({ attempt, number }) => ({ kind: "attempt" as const, attempt, number })));
    }
    pending = [];
  };
  attempts.forEach((attempt, index) => {
    const number = index + 1;
    if (!isParallel && isFailedStatus(attempt.status) && attempt.run_id !== standAlone) {
      pending.push({ attempt, number });
      return;
    }
    flush();
    rows.push({ kind: "attempt", attempt, number });
  });
  flush();
  return rows;
}

// The words a tool uses when a line is the failure itself, not narration of it.
// "Error" may end a name (AssertionError, TypeError), so it needs no boundary before it.
const ERROR_LINE = /(errors?|exception)\b|\b(fatal|traceback|panic|denied|refused|not found|too long|exceeded)\b|failed:/i;

/** A line of run output that states an error, for the gutter mark. */
export function isErrorLine(line: string): boolean {
  return ERROR_LINE.test(line);
}

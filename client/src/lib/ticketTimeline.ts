/**
 * A ticket's history as one story: each stage visit from the run ledger, with
 * the outputs that landed while it ran.
 *
 * The artifact pane used to tell this story four times — a flat artifact feed,
 * a run list, a stage ledger and an errors list — and left the reader to join
 * them by eye. This does the join once.
 *
 * Placement, most certain first:
 *  1. the row's run is an attempt in a visit — that visit;
 *  2. the row names a stage — the latest visit of that stage begun by then;
 *  3. otherwise the visit that was under way when it landed.
 * Rule 3 is chronology, not attribution: it says what was running, which is
 * all an untagged row can honestly be placed by. Rows written before any run
 * are kept apart rather than pinned to the first stage.
 */

import type { LedgerAttempt, LedgerVisit, TicketArtifactItem } from "../api/types";
import { ACTIVE_LEDGER_STATUSES } from "./ledgerStatus";
import { parseTimestamp } from "./timestamps";

// `cancelled` is a person or the platform stopping a run, not the work failing.
const FAILED_STATUSES = new Set(["failed"]);

export interface TimelineVisit {
  /** Stable across polls: a stage plus which time the pipeline entered it. */
  key: string;
  visit: LedgerVisit;
  startedAt: Date | null;
  /** Null while any attempt is still in flight. */
  endedAt: Date | null;
  /** Wall-clock span, so parallel lanes are not summed into a fiction. */
  durationSeconds: number | null;
  active: boolean;
  failedAttempts: LedgerAttempt[];
  /** Oldest first, system records excluded. */
  outputs: TicketArtifactItem[];
  errorCount: number;
}

export interface TicketTimeline {
  /** Outputs older than the first run — usually what triage or a person attached. */
  beforeFirstRun: TicketArtifactItem[];
  visits: TimelineVisit[];
}

function itemTime(item: TicketArtifactItem): number {
  return parseTimestamp(item.created_at)?.getTime() ?? Number.NEGATIVE_INFINITY;
}

function visitStart(visit: LedgerVisit): Date | null {
  let earliest: Date | null = null;
  for (const attempt of visit.attempts) {
    const at = parseTimestamp(attempt.started_at);
    if (at && (!earliest || at < earliest)) earliest = at;
  }
  return earliest;
}

function visitEnd(visit: LedgerVisit): Date | null {
  let latest: Date | null = null;
  for (const attempt of visit.attempts) {
    if (ACTIVE_LEDGER_STATUSES.has(attempt.status)) return null;
    const at = parseTimestamp(attempt.finished_at);
    if (at && (!latest || at > latest)) latest = at;
  }
  return latest;
}

export function isErrorOutput(item: TicketArtifactItem): boolean {
  return item.kind === "error";
}

/** Where each output sits, by visit index; -1 for before the first run. */
export function placeOutputs(
  visits: readonly LedgerVisit[],
  items: readonly TicketArtifactItem[],
): Map<string, number> {
  const starts = visits.map((visit) => visitStart(visit)?.getTime() ?? null);
  const visitByRun = new Map<string, number>();
  visits.forEach((visit, index) => {
    for (const attempt of visit.attempts) visitByRun.set(attempt.run_id, index);
  });

  // The latest visit (optionally of one stage) that had begun by `at`.
  const latestBegunBy = (at: number, stageKey: string | null): number => {
    let found = -1;
    starts.forEach((start, index) => {
      if (start === null || start > at) return;
      if (stageKey !== null && visits[index].stage_key !== stageKey) return;
      if (found === -1 || start >= (starts[found] ?? Number.NEGATIVE_INFINITY)) found = index;
    });
    return found;
  };

  const placed = new Map<string, number>();
  for (const item of items) {
    const byRun = item.run_id ? visitByRun.get(item.run_id) : undefined;
    if (byRun !== undefined) {
      placed.set(item.id, byRun);
      continue;
    }
    const at = itemTime(item);
    if (item.stage_key) {
      const byStage = latestBegunBy(at, item.stage_key);
      if (byStage !== -1) {
        placed.set(item.id, byStage);
        continue;
      }
    }
    placed.set(item.id, latestBegunBy(at, null));
  }
  return placed;
}

export function buildTicketTimeline(
  visits: readonly LedgerVisit[],
  items: readonly TicketArtifactItem[],
): TicketTimeline {
  const outputs = items.filter((item) => !item.system);
  const placement = placeOutputs(visits, outputs);
  const buckets: TicketArtifactItem[][] = visits.map(() => []);
  const beforeFirstRun: TicketArtifactItem[] = [];
  for (const item of outputs) {
    const index = placement.get(item.id) ?? -1;
    (index === -1 ? beforeFirstRun : buckets[index]).push(item);
  }
  const oldestFirst = (a: TicketArtifactItem, b: TicketArtifactItem) => itemTime(a) - itemTime(b);
  beforeFirstRun.sort(oldestFirst);

  return {
    beforeFirstRun,
    visits: visits.map((visit, index) => {
      const startedAt = visitStart(visit);
      const endedAt = visitEnd(visit);
      const bucket = buckets[index].sort(oldestFirst);
      return {
        key: `${visit.stage_key}#${visit.visit_number}`,
        visit,
        startedAt,
        endedAt,
        durationSeconds:
          startedAt && endedAt ? Math.max(0, (endedAt.getTime() - startedAt.getTime()) / 1000) : null,
        active: ACTIVE_LEDGER_STATUSES.has(visit.status),
        failedAttempts: visit.attempts.filter((attempt) => FAILED_STATUSES.has(attempt.status)),
        outputs: bucket,
        errorCount: bucket.filter(isErrorOutput).length,
      };
    }),
  };
}

/** What a stage report concluded, when the output is one. */
export interface ReportVerdict {
  status: string;
  confidence: number | null;
}

export function reportVerdict(content: unknown): ReportVerdict | null {
  if (typeof content !== "object" || content === null) return null;
  const record = content as Record<string, unknown>;
  if (typeof record.status !== "string" || typeof record.stage_key !== "string") return null;
  return {
    status: record.status,
    confidence: typeof record.confidence === "number" ? record.confidence : null,
  };
}

export function isFailedStatus(status: string): boolean {
  return FAILED_STATUSES.has(status);
}

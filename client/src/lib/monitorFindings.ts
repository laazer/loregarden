import type { MonitorFinding } from "../api/types";

/**
 * Grouping for the workspace-wide monitor view.
 *
 * A module of its own rather than a second export from the component: a pure
 * function is worth testing directly, and a component file that also exports
 * helpers breaks fast refresh for the whole file.
 */

export interface FindingGroup {
  key: string;
  condition: string;
  stageKey: string;
  findings: MonitorFinding[];
  /** Distinct tickets — the recurrence that means "pipeline fault". */
  tickets: number;
  /** Earliest first_seen in the group: how long this has been true. */
  firstSeen: string | null;
  /** Latest last_seen: whether it is still true. */
  lastSeen: string | null;
}

/**
 * `MonitorFinding.occurrences` is deliberately absent from this model.
 *
 * It counts **sweep ticks that still observed the condition**, not events:
 * `record_findings` increments it once per reconcile pass while the condition
 * holds. Against the live database every finding read 5989 — two days of ticks —
 * and rendering that as "5989x" states, in the most emphatic available unit,
 * something that never happened. Unit fixtures said 1 and 4, so nothing caught
 * it until the real endpoint was called.
 *
 * What the number was reaching for is duration, and `firstSeen`/`lastSeen` say
 * that in a unit a reader can act on.
 */
export function groupFindings(findings: MonitorFinding[]): FindingGroup[] {
  const groups = new Map<string, FindingGroup>();
  for (const finding of findings) {
    const key = `${finding.condition}:${finding.stage_key}`;
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        key,
        condition: finding.condition,
        stageKey: finding.stage_key,
        findings: [finding],
        tickets: 0,
        firstSeen: finding.first_seen,
        lastSeen: finding.last_seen,
      });
      continue;
    }
    existing.findings.push(finding);
    if (finding.first_seen && (!existing.firstSeen || finding.first_seen < existing.firstSeen)) {
      existing.firstSeen = finding.first_seen;
    }
    if (finding.last_seen && (!existing.lastSeen || finding.last_seen > existing.lastSeen)) {
      existing.lastSeen = finding.last_seen;
    }
  }
  for (const group of groups.values()) {
    // Distinct tickets. A workspace-scoped finding carries no ticket id and
    // contributes none — it is already about the workspace rather than about a
    // ticket that recurred.
    group.tickets = new Set(group.findings.map((f) => f.ticket_id).filter(Boolean)).size;
  }
  // Most tickets first, then longest-standing. The ordering IS the
  // recommendation, since this surface offers no severity of its own.
  return [...groups.values()].sort(
    (a, b) =>
      b.tickets - a.tickets ||
      b.findings.length - a.findings.length ||
      (a.firstSeen ?? "").localeCompare(b.firstSeen ?? ""),
  );
}

/** Turns the condition slug into something a person reads. */
export function conditionLabel(condition: string): string {
  return condition.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * What each condition means for the person reading it, in one sentence. The
 * slug says what the detector measured; this says why anyone should care.
 */
const CONDITION_MEANINGS: Record<string, string> = {
  stage_thrash: "A stage ran far more times than usual in one run — usually a review ↔ implement rework loop.",
  unbudgeted_repeat: "A stage was retried with no orchestration run behind it, so no retry cap applied.",
  failure_cluster: "This stage fails much more often than the rest of its workspace — look at its agent or template.",
  stalled_run: "A run is still going well past the longest this stage has ever taken.",
  draft_drift: "A Studio draft no longer matches the template it publishes to.",
  skip_condition_rot: "A skip_when names a condition nothing resolves, so the stage is never skipped.",
  stale_cursor: "The ticket's stage cursor points at a stage its workflow does not have.",
  emptied_group: "Every alternative in a stage group was pruned, so the group finished with no work.",
  unsettled_stage: "The stage is blocked even though its agent run succeeded.",
  timeout_floor_stale: "This stage's timeout is below how long it really takes (measured p95).",
  harness_failure_cluster: "Most recent failures are harness interruptions, not the agents' work.",
};

export function conditionMeaning(condition: string): string | null {
  return CONDITION_MEANINGS[condition] ?? null;
}

/**
 * Whether a finding is about something that can still be acted on.
 *
 * A finding persists after its ticket finishes — a `stage_thrash` recorded in
 * a run three weeks ago is still "observed" on every sweep, because the run's
 * history does not change. On the live database (2026-09-28) 85 of the 94
 * ticket findings sat on done tickets, burying the nine about work still moving.
 * Workspace-scoped findings (no ticket) are recomputed per request, so they
 * are always current.
 */
export function isLiveFinding(finding: MonitorFinding): boolean {
  if (!finding.ticket_id) return true;
  return finding.ticket_state !== "done" && finding.ticket_state !== "wont_do";
}

export interface TicketFindings {
  ticketId: string;
  externalId: string;
  title: string;
  state: MonitorFinding["ticket_state"];
  workspaceSlug: string;
  /** One line per (condition, stage), however many runs reported it. */
  lines: { key: string; condition: string; stageKey: string; summary: string; count: number }[];
  lastSeen: string | null;
}

/**
 * Findings folded under the ticket they are about. The same condition on the
 * same stage across several runs of one ticket is one line with a count — the
 * old list printed "Stage 'review' ran 4 times…" once per run, a dozen times.
 * Newest-seen ticket first.
 */
export function groupByTicket(findings: MonitorFinding[]): TicketFindings[] {
  const tickets = new Map<string, TicketFindings>();
  for (const finding of findings) {
    if (!finding.ticket_id) continue;
    let entry = tickets.get(finding.ticket_id);
    if (!entry) {
      entry = {
        ticketId: finding.ticket_id,
        externalId: finding.ticket_external_id,
        title: finding.ticket_title,
        state: finding.ticket_state,
        workspaceSlug: finding.workspace_slug,
        lines: [],
        lastSeen: finding.last_seen,
      };
      tickets.set(finding.ticket_id, entry);
    }
    if (finding.last_seen && (!entry.lastSeen || finding.last_seen > entry.lastSeen)) {
      entry.lastSeen = finding.last_seen;
    }
    const key = `${finding.condition}:${finding.stage_key}`;
    const line = entry.lines.find((existing) => existing.key === key);
    if (line) {
      line.count += 1;
    } else {
      entry.lines.push({
        key,
        condition: finding.condition,
        stageKey: finding.stage_key,
        summary: finding.summary,
        count: 1,
      });
    }
  }
  return [...tickets.values()].sort((a, b) => (b.lastSeen ?? "").localeCompare(a.lastSeen ?? ""));
}

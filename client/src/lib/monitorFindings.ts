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

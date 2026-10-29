/**
 * Agent Studio's index: every agent with how its runs have gone, ranked so the
 * one whose prompt most needs reading is on top.
 *
 * Two thresholds, both about not crying wolf. An agent with fewer than
 * `MIN_RUNS_FOR_RATE` finished runs has a rate but not a reliable one (a single
 * failed run is 100%), so it is shown muted and ranks below every agent with
 * enough runs. And a rate is marked as a problem only from `FAILURE_RATE_ALERT`,
 * so the colour lands on the few agents that need it rather than on all of them.
 */

import type { AgentRunStats, StudioAgent } from "../api/types";
import type { SortDirection } from "./indexSort";

export const MIN_RUNS_FOR_RATE = 10;
export const FAILURE_RATE_ALERT = 0.15;

export interface AgentIndexRow {
  agent: StudioAgent;
  runs: number;
  failed: number;
  /** Failed over finished runs; null when it has never finished one. */
  rate: number | null;
  /** True when `rate` rests on enough runs to rank and alert on. */
  rateReliable: boolean;
  lastRunAt: string | null;
}

export type AgentSortKey = "name" | "runs" | "failed" | "rate" | "lastRun";

export function agentIndexRows(
  agents: readonly StudioAgent[],
  stats: readonly AgentRunStats[],
): AgentIndexRow[] {
  const byId = new Map(stats.map((row) => [row.agent_id, row]));
  return agents.map((agent) => {
    const row = byId.get(agent.slug);
    const runs = row?.runs ?? 0;
    const failed = row?.failed ?? 0;
    return {
      agent,
      runs,
      failed,
      rate: runs > 0 ? failed / runs : null,
      rateReliable: runs >= MIN_RUNS_FOR_RATE,
      lastRunAt: row?.last_run_at ?? null,
    };
  });
}

/** Whole percent, as the table prints it. */
export function ratePercent(rate: number): number {
  return Math.round(rate * 100);
}

/**
 * Compared on the printed percent, not the raw ratio: 44 of 301 is 14.6% and
 * prints as "15%", and a "15%" left unflagged under a 15% rule reads as a bug.
 */
export function isFailureAlert(row: AgentIndexRow): boolean {
  return (
    row.rateReliable && row.rate !== null && ratePercent(row.rate) >= ratePercent(FAILURE_RATE_ALERT)
  );
}

/** The rate as ranked: unreliable and missing rates sort below every reliable one. */
function rankedRate(row: AgentIndexRow): number {
  if (row.rate === null) return -2;
  return row.rateReliable ? row.rate : -1 + row.rate / 2;
}

const COMPARE: Record<AgentSortKey, (a: AgentIndexRow, b: AgentIndexRow) => number> = {
  name: (a, b) => a.agent.name.localeCompare(b.agent.name),
  runs: (a, b) => a.runs - b.runs,
  failed: (a, b) => a.failed - b.failed,
  rate: (a, b) => rankedRate(a) - rankedRate(b),
  lastRun: (a, b) => (a.lastRunAt ?? "").localeCompare(b.lastRunAt ?? ""),
};

/** Sorted by `key`; ties go to the agent with more runs, then by name. */
export function sortAgentRows(
  rows: readonly AgentIndexRow[],
  key: AgentSortKey,
  direction: SortDirection,
): AgentIndexRow[] {
  const sign = direction === "ascending" ? 1 : -1;
  return [...rows].sort(
    (a, b) =>
      sign * COMPARE[key](a, b) ||
      b.runs - a.runs ||
      a.agent.name.localeCompare(b.agent.name),
  );
}

export function matchesAgentFilter(row: AgentIndexRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [row.agent.name, row.agent.slug, row.agent.description].some((text) =>
    text.toLowerCase().includes(needle),
  );
}

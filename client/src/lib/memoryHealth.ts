/**
 * What the memory-briefing telemetry says, in words.
 *
 * The page must not leave the operator to subtract two timestamps: a
 * `last_row_at` far behind `newest_run_at` means the recording seam stopped
 * writing, and that is the failure the telemetry exists to expose.
 */

import type { HealthShare, MemoryBriefingStats } from "../api/memoryApi";
import { parseTimestamp } from "./timestamps";

/**
 * How far the last briefing row may trail the newest run before the seam is
 * called dead. A briefing is written at prompt assembly, seconds after a run
 * starts; fifteen minutes is slack for a slow start, not for a missing write.
 */
export const SEAM_LAG_LIMIT_MS = 15 * 60 * 1000;

/** The windows the health page offers, in days. */
export const WINDOW_CHOICES = [7, 30, 90] as const;

export type SeamTone = "none" | "ok" | "dead";

export interface SeamVerdict {
  tone: SeamTone;
  text: string;
}

/** Zeros and null timestamps: nothing ran. Not an error, and not "healthy". */
export function isEmptyWindow(stats: MemoryBriefingStats): boolean {
  return stats.runs_in_window === 0 && stats.rows_in_window === 0;
}

/** "3 days", "4 hours", "12 minutes", "under a minute". */
export function describeDuration(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "under a minute";
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  if (minutes < 60) return plural(minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return plural(hours, "hour");
  return plural(Math.floor(hours / 24), "day");
}

export function seamVerdict(stats: MemoryBriefingStats): SeamVerdict {
  if (isEmptyWindow(stats)) {
    return { tone: "none", text: `No runs started in the last ${stats.window_days} days.` };
  }
  const newestRun = parseTimestamp(stats.newest_run_at);
  const lastRow = parseTimestamp(stats.last_row_at);
  if (!lastRow) {
    return {
      tone: "dead",
      text:
        `${stats.runs_in_window} runs started in this window and not one recorded a briefing. ` +
        "The recording seam is not writing.",
    };
  }
  const lag = newestRun ? newestRun.getTime() - lastRow.getTime() : 0;
  if (lag > SEAM_LAG_LIMIT_MS) {
    return {
      tone: "dead",
      text:
        `The last briefing was recorded ${describeDuration(lag)} before the newest run ` +
        "started. Recording appears to have stopped since then.",
    };
  }
  return { tone: "ok", text: "Recording is keeping up with runs." };
}

/** Share of runs in the window that recorded nothing, as a whole percentage. */
export function holeShare(stats: MemoryBriefingStats): number {
  if (stats.runs_in_window === 0) return 0;
  return Math.round((stats.runs_with_no_briefing_row / stats.runs_in_window) * 100);
}

/** What each graph-health share means, for the panel's labels and hints. */
export const HEALTH_SHARE_LABELS: Record<HealthShare, { label: string; hint: string }> = {
  unlinked: {
    label: "Unlinked",
    hint: "no relation to any other learning — the related digest can never reach it",
  },
  never_surfaced: {
    label: "Never briefed",
    hint: "not injected into a single run's briefing yet",
  },
  contested: {
    label: "Contested",
    hint: "holds a contradiction nobody resolved; agents are briefed with both sides",
  },
  stale: { label: "Stale", hint: "not updated in 90 days" },
  surfaced_unscored: {
    label: "Unscored",
    hint: "briefed into runs, none of which has concluded with an outcome yet",
  },
};

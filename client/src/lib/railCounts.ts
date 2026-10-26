/**
 * The live counts the rail draws beside a page's name.
 *
 * Each one answers "is there a reason to open this page now?" without opening
 * it, and is built only from data the app already holds: the queue feed every
 * page subscribes to, and the board summary Home polls. A count that is zero
 * is not drawn, because a column of zeros reads as noise and hides the one
 * number that is not; the lane fraction is the exception, since "0/3" is the
 * answer to "is anything using the machine?".
 */

import type { TicketStatusSummary } from "../api/types";
import type { AppPage } from "./appNavigation";
import type { ParallelStats } from "./queueSocket";

export interface RailCount {
  /** What the rail shows, e.g. `21` or `1/3`. */
  text: string;
  /** What a screen reader hears as the link's description, e.g. `21 need you`. */
  label: string;
  /** `you` when the count is people being waited on; drawn in the violet of that tone. */
  tone: "you" | "plain";
}

export interface RailCountSources {
  /** Board-wide ticket counts; undefined until the summary first lands. */
  summary: TicketStatusSummary | undefined;
  /** Tickets holding an execution slot right now. */
  runningCount: number;
  stats: ParallelStats;
  /**
   * Whether `stats` is a real snapshot. Before the first one lands, and while
   * the feed is down, it holds defaults (three free lanes), and drawing those
   * would state a fact nobody measured.
   */
  queueLive: boolean;
}

export function railCounts({
  summary,
  runningCount,
  stats,
  queueLive,
}: RailCountSources): Partial<Record<AppPage, RailCount>> {
  const counts: Partial<Record<AppPage, RailCount>> = {};

  if (runningCount > 0) {
    counts.dashboard = {
      text: String(runningCount),
      label: `${runningCount} running`,
      tone: "plain",
    };
  }

  // Parked tickets are owed by a person, and awaiting ones are stopped on a
  // question or a permission: both are waiting on the operator, nothing else.
  const needYou = summary ? (summary.parked ?? 0) + summary.awaiting : 0;
  if (needYou > 0) {
    counts["work-items"] = {
      text: String(needYou),
      label: `${needYou} need you`,
      tone: "you",
    };
  }

  if (queueLive && stats.max_concurrent > 0) {
    const busy = Math.min(stats.active_count, stats.max_concurrent);
    counts.queue = {
      text: `${busy}/${stats.max_concurrent}`,
      label: `${busy} of ${stats.max_concurrent} lanes busy`,
      tone: "plain",
    };
  }

  return counts;
}

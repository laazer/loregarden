/**
 * What a run's log pane shows, decided once for both panes that show it.
 *
 * `RunLogModal` and `logs/LaneLogView` are separate components rendering one
 * `GET /api/runs/{id}/log` payload, so a divergence here is how one pane keeps
 * the old behaviour: it blanks during a control-plane restart while the other
 * does not. The state that matters is `reconnecting` — a failed refetch must
 * never replace lines that are already on screen, because that reports the run
 * as gone at exactly the 10-30 seconds a detached run is still working.
 *
 * `isRunning` rather than `status`: the two panes do not share a status
 * vocabulary (the modal's `ACTIVE_STATUSES` includes `awaiting_permission`,
 * the lane's `ACTIVE_LEDGER_STATUSES` includes `queued`), so each applies its
 * own set and what is shared is the decision and the words.
 */

export const LOG_FEED_LOADING = "Loading log…";
export const LOG_FEED_ERROR = "Could not load this run’s log.";
export const LOG_FEED_RECONNECTING = "Reconnecting to the control plane…";
export const LOG_FEED_EMPTY = "No log recorded for this run.";

/**
 * Only ever called with a transport the run record actually names — never a
 * guessed default, because 1,449 existing runs have no process identity and
 * rendering those as "file" asserts something the row does not say.
 */
export function detachedEmptyText(transport: string): string {
  return `No output yet — the agent is running detached on ${transport}.`;
}

export type LogFeedState =
  | "loading"
  | "error"
  | "reconnecting"
  | "empty"
  | "empty-detached"
  | "feed";

export interface LogFeedInput {
  /** Rendered lines from the payload; only the count is read. */
  lines: readonly unknown[];
  /** The partial line a streaming run is mid-way through, if any. */
  live: string | null;
  /** react-query's first-fetch flag — false on every later poll. */
  isPending: boolean;
  isError: boolean;
  /** The pane's own reading of "this run could still say something". */
  isRunning: boolean;
  /** `agent_transport`, or "" for a run whose row does not say. */
  transport: string;
}

export function logFeedState({
  lines,
  live,
  isPending,
  isError,
  isRunning,
  transport,
}: LogFeedInput): LogFeedState {
  const hasContent = lines.length > 0 || Boolean(live);
  if (isPending) return "loading";
  // The blank error state is for a pane with nothing to lose. With content on
  // screen the feed stands and the failure is one row above it.
  if (isError) return hasContent ? "reconnecting" : "error";
  if (hasContent) return "feed";
  if (isRunning && transport) return "empty-detached";
  return "empty";
}

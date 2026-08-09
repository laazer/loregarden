/**
 * The wire between the queue dashboard and the control plane's queue socket.
 *
 * Kept out of React so the protocol — framing, reconnect, what "connected"
 * means — can be tested without rendering anything, and so a bug in it does
 * not need a component to reproduce.
 *
 * Replaces a Socket.IO client that could never connect: nothing on the server
 * ever instantiated the Flask-SocketIO server it dialled, so it sat in
 * 'connecting' forever and the dashboard's badge could only ever read
 * "Polling".
 */

import { ReconnectingSocket, type SocketStatus } from "./reconnectingSocket";

/**
 * The names behind the ids, resolved server-side in `queue_status.py`.
 *
 * Optional because a snapshot from an older backend will not carry them, and a
 * slot card showing a stale-but-readable id beats one that crashes.
 */
export interface TicketHierarchyNode {
  id: string;
  code: string;
  title: string;
  work_item_type?: string;
}

export interface RunLabels {
  ticket_title?: string;
  ticket_code?: string;
  ticket_state?: string;
  /**
   * Whether an agent is actually on the ticket. The queue knows who holds a
   * slot, not whether the work behind it is still moving — see
   * services/ticket_activity.py.
   */
  ticket_activity?: string;
  /**
   * Root → ticket path, same chain the work-items tree walks. Empty when the
   * ticket is a root or the backend has not labelled it yet.
   */
  ticket_ancestry?: TicketHierarchyNode[];
  /**
   * Deepest live nested ticket under this lane holder. A parent orchestration
   * keeps the slot while a child execute does the work — without this the card
   * only names the parent.
   */
  running_descendant?: TicketHierarchyNode | null;
  agent_name?: string;
  stage_key?: string;
  /**
   * Which workspace this run belongs to. The slot pool is shared across all of
   * them, so without this a board of three cards says nothing about whose work
   * is running.
   */
  workspace_name?: string;
  workspace_slug?: string;
}

/**
 * What history says is still to come for a ticket *and everything under it*.
 *
 * A lane runs a whole ticket, and a ticket's children run with it, so this is
 * the only figure that describes what a lane card is actually doing. `work` is
 * every remaining stage summed; `critical_path` is the part more lanes cannot
 * shorten; `projected` is the two reconciled against the slots that exist.
 */
export interface TicketTreeEstimate {
  work_seconds: number | null;
  critical_path_seconds: number | null;
  projected_seconds: number | null;
  ticket_count: number;
  /** Tickets in the subtree with no history to price them. `projected` is then a floor. */
  unknown_tickets: number;
  stage_count: number;
}

/** Timing fields carried by anything holding or awaiting a slot. */
interface RunEstimates {
  /**
   * The whole span this card measures against — for a lane, elapsed plus what
   * is left of the ticket tree. Null when there is no history to learn from;
   * never substitute a default, that is the fabrication this replaced.
   */
  estimated_duration_seconds?: number | null;
  /** What is still to come. Null for the same reason. */
  estimated_remaining_seconds?: number | null;
  /** Present when this card is a whole ticket rather than a single stage. */
  ticket_tree_estimate?: TicketTreeEstimate | null;
}

export interface ActiveRun extends RunLabels, RunEstimates {
  run_id: string;
  ticket_id: string;
  /**
   * Set when a whole ticket's pipeline holds the slot, rather than one stage.
   * The card's status is then the pipeline's — a lane keeps the slot between
   * stages, so the last stage's "succeeded" is not what the lane is doing.
   */
  orchestration_run_id?: string;
  slot_number: number;
  elapsed_seconds: number;
  status: string;
  agent_id: string;
}

export interface QueuedRun extends RunLabels, RunEstimates {
  run_id: string;
  ticket_id: string;
  position: number;
  estimated_start_at: string;
  wait_seconds: number;
  agent_id: string;
  /** Projected seconds until this starts. Null with no history to project from. */
  estimated_wait_seconds?: number | null;
}

/** A ticket waiting its turn in a lane. */
export interface LaneEntry extends RunLabels, RunEstimates {
  entry_id: string;
  ticket_id: string;
  workspace_id: string;
  slot_number?: number;
  position: number;
  auto_approve: boolean;
  stop_at_stage_key: string;
  queued_at: string | null;
  /**
   * Projected seconds until this entry starts — everything ahead of it *in its
   * own lane*, since a lane is a serial pipeline. Distinct from `wait_seconds`,
   * which is how long it has already waited.
   */
  estimated_wait_seconds?: number | null;
  wait_seconds?: number;
}

/**
 * A ticket that blocked or failed in this lane and has not been acknowledged.
 *
 * The board only ever showed live entries, so a lane that had just eaten a
 * ticket looked exactly like an idle one. These stay on the lane card until
 * someone dismisses them — `outcome` is derived from the orchestration run, not
 * from the entry's own queue status, which is a trap (`started` is terminal).
 */
export interface LaneAttentionEntry {
  entry_id: string;
  ticket_id: string;
  ticket_external_id: string;
  ticket_title: string;
  ticket_state: string;
  workspace_id: string;
  workspace_slug: string;
  workspace_name: string;
  slot_number: number;
  outcome: "blocked" | "failed";
  run_code: string;
  last_stage_key: string;
  failure_reason: string;
  retry_count: number;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

/** One execution slot: what runs in it, what is queued behind it, what went wrong in it. */
export interface QueueLane {
  slot_number: number;
  running: ActiveRun | null;
  waiting: LaneEntry[];
  /** Capped for payload size; `attention_total` is the true count. */
  attention?: LaneAttentionEntry[];
  attention_total?: number;
}

export interface ParallelStats {
  max_concurrent: number;
  active_count: number;
  available_slots: number;
  queued_count: number;
  total_slots_occupied: number;
  /**
   * How long the oldest waiting entry has *already* waited. Measured, not
   * projected — and in seconds, because the minutes this replaced rendered
   * every wait under a minute as "0m".
   */
  longest_wait_seconds: number;
}

/** What to show before the first snapshot arrives. */
export const DEFAULT_PARALLEL_STATS: ParallelStats = {
  max_concurrent: 3,
  active_count: 0,
  available_slots: 3,
  queued_count: 0,
  total_slots_occupied: 0,
  longest_wait_seconds: 0,
};

/** What `/api/parallel/status` returns, and what the socket pushes. */
export interface QueueStatusSnapshot {
  active_runs: ActiveRun[];
  queued_runs: QueuedRun[];
  /** Each slot with its own pipeline. The board renders these. */
  lanes: QueueLane[];
  available_slots: number;
  total_slots: number;
  queue_length: number;
  /** Projected seconds until the queue empties, or null with no run history. */
  estimated_clear_seconds?: number | null;
  /**
   * Longest projected wait before something still queued starts. Forward-looking,
   * unlike `stats.longest_wait_seconds`.
   */
  estimated_wait_seconds?: number | null;
  stats: ParallelStats;
}

/**
 * Something happened, as opposed to something is the case.
 *
 * The snapshot describes the queue as it stands; it cannot say that a run just
 * finished, which is what a notification is about. The server forwards these
 * alongside each snapshot — see NOTIFIABLE_EVENTS in `queue_events.py`.
 */
export interface QueueEvent {
  type: "queue_promoted" | "run_completed" | "error";
  timestamp?: string;
  data?: {
    runId?: string;
    slotNumber?: number;
    status?: string;
    message?: string;
    code?: string;
    ticketId?: string;
    ticketTitle?: string;
    stageKey?: string;
    agentId?: string;
  };
}

/** Three states, not four — see `SocketStatus`. */
export type QueueSocketStatus = SocketStatus;

export interface QueueSocketHandlers {
  onSnapshot: (snapshot: QueueStatusSnapshot) => void;
  onStatus: (status: QueueSocketStatus) => void;
  /** Optional: a caller that only renders state need not handle events. */
  onEvent?: (event: QueueEvent) => void;
}

/** First reconnect delay, doubling from here. */
export const BASE_RECONNECT_DELAY_MS = 1000;

/** Ceiling for the backoff. Long enough to be polite, short enough that a
 * backend restart is picked up while the operator is still looking. */
export const MAX_RECONNECT_DELAY_MS = 30000;

export function queueSocketUrl(apiBase: string): string {
  const base = apiBase.replace(/\/$/, "").replace(/^http/, "ws");
  // One socket, not one per workspace: the queue it reports is shared.
  return `${base}/ws/queue`;
}

/**
 * A queue subscription that keeps trying.
 *
 * Reconnects, unlike TerminalSocket — there is no session to lose here, only a
 * snapshot to refresh, and the control plane restarts on every backend edit.
 * Callers are told the truth about the connection at every point so they can
 * poll while it is down instead of pretending.
 */
export class QueueSocket extends ReconnectingSocket<QueueSocketHandlers> {
  protected readonly policy = {
    baseDelayMs: BASE_RECONNECT_DELAY_MS,
    maxDelayMs: MAX_RECONNECT_DELAY_MS,
  };

  protected handleMessage(raw: unknown): void {
    const message = raw as {
      type?: string;
      data?: QueueStatusSnapshot | QueueEvent;
    };
    if (message?.type === "queue_status" && message.data) {
      this.handlers.onSnapshot(message.data as QueueStatusSnapshot);
    } else if (message?.type === "queue_event" && message.data) {
      this.handlers.onEvent?.(message.data as QueueEvent);
    }
  }
}

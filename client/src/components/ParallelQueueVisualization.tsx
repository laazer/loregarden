/**
 * The queue, as lanes.
 *
 * Each slot is a serial pipeline: what is running in it, and the tickets queued
 * behind it. Adding a ticket to an idle lane starts it; adding to a busy one
 * puts it in line, and it starts on its own when the lane frees. There is no
 * Start button and nothing staged in the browser — adding is committing, which
 * is the only way a queued entry can start itself later.
 *
 * Two things here used to be invented. Every progress bar measured against a
 * hardcoded 300-second run, and "estimated clear" was 300 plus 300 per queued
 * run — so a two-second run and a twenty-minute run drew identical bars, and a
 * queue of three across three slots read as three times the wait it was. Both
 * now come from the server's median of what that agent has actually taken, and
 * when there is no history to draw on the UI says so rather than guessing.
 *
 * What they were replaced *with* was still not the truth. A lane runs a whole
 * ticket and everything under it, so a card priced at one agent's median said
 * four minutes for a feature with nine child tasks behind it, and every lane
 * drew the indeterminate bar because no single median could describe it. The
 * server now sends the remaining pipeline of the whole subtree, and a wait is
 * measured down the entry's own lane rather than across the pool — a lane is
 * serial, so position 3 does not start when some other lane drains.
 */

import { useMemo, useState } from 'react';
import { IconCloseButton } from './IconCloseButton';
import { OverflowMenu, OverflowMenuItem } from './OverflowMenu';
import { QueueSlotTicketPicker } from './QueueSlotTicketPicker';
import { QueueAddToLaneModal, type QueueAddRequest } from './QueueAddToLaneModal';
import {
  useRunningChildTickets,
  type RunningChildTicket,
} from '../hooks/useRunningChildTickets';
import { useQueueStatus } from '../state/QueueStatusContext';
import { queueLanesApi } from '../lib/queueLanesApi';
import type {
  LaneAttentionEntry,
  LaneEntry,
  QueueLane,
  TicketHierarchyNode,
  TicketTreeEstimate,
} from '../lib/queueSocket';
import { navigateToTicket } from '../lib/useAppNavigation';
import {
  runStatusLabel,
  ticketActivityColor,
  ticketActivityLabel,
  ticketStateColor,
  ticketStateLabel,
} from '../lib/ticketStates';
import './ParallelQueueVisualization.css';

/**
 * Statuses where the run behind a slot is still alive. Anything else — it
 * succeeded, failed, was cancelled — means the lane is holding a slot for work
 * that has stopped, and the card has to say so instead of drawing a timer.
 */
const LIVE_RUN_STATUSES = new Set(['running', 'awaiting_permission']);

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  // Whole ticket trees run for hours, and "218m 4s" is a number nobody reads.
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.round((seconds % 3600) / 60);
    return mins ? `${hours}h ${mins}m` : `${hours}h`;
  }
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${minutes}m ${secs}s`;
}

/**
 * "~2h 10m", or nothing at all.
 *
 * The tilde is the point: every one of these is a median projection, and a
 * bare figure reads as a deadline the queue never promised.
 */
function formatEstimate(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds <= 0) return 'now';
  return `~${formatDuration(seconds)}`;
}

/**
 * What a lane card says about the tree behind it.
 *
 * The count matters as much as the time: "3 tickets" is why the estimate is
 * an hour and not four minutes, and `unknown_tickets` is the admission that
 * part of the subtree has no history to price it — the figure is then a floor,
 * and saying so is the whole difference from making one up.
 */
function treeSummary(estimate: TicketTreeEstimate | null | undefined): string | null {
  if (!estimate || estimate.ticket_count <= 1) return null;
  const parts = [`${estimate.ticket_count} tickets`];
  if (estimate.stage_count) parts.push(`${estimate.stage_count} stages left`);
  if (estimate.unknown_tickets) parts.push(`${estimate.unknown_tickets} unestimated`);
  return parts.join(' · ');
}

/**
 * Work-items style path: indented `code · title` rows, leaf highlighted.
 *
 * When a parent holds the lane and a nested child is actually executing, the
 * chain is ancestry plus that descendant so the card names the real work.
 */
function TicketHierarchy({
  ancestry,
  runningDescendant,
}: {
  ancestry?: TicketHierarchyNode[];
  runningDescendant?: TicketHierarchyNode | null;
}) {
  const chain = [...(ancestry ?? [])];
  if (
    runningDescendant &&
    (!chain.length || chain[chain.length - 1]?.id !== runningDescendant.id)
  ) {
    chain.push(runningDescendant);
  }

  if (chain.length < 2) return null;

  return (
    <div className="queue-hierarchy" aria-label="Ticket hierarchy">
      {chain.map((node, index) => {
        const isLeaf = index === chain.length - 1;
        return (
          <div
            key={node.id}
            className={`queue-hierarchy-node${isLeaf ? ' is-leaf' : ''}`}
            style={{ paddingLeft: index * 10 }}
            title={node.title}
          >
            {node.code ? (
              <>
                <span className="queue-hierarchy-code">{node.code}</span>
                {node.title ? <span className="queue-hierarchy-sep"> · </span> : null}
              </>
            ) : null}
            {node.title ? <span className="queue-hierarchy-title">{node.title}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The ticket's own two axes, on the lane card.
 *
 * A lane says where a ticket sits in the queue; these say what the ticket
 * itself is — open or blocked, and whether anything is executing on it. A
 * queued entry whose ticket is already running elsewhere, or a slot whose
 * ticket has gone blocked, was previously indistinguishable from a healthy one.
 */
function TicketStateChips({
  state,
  activity,
}: {
  state?: string;
  activity?: string;
}) {
  if (!state && !activity) return null;
  return (
    <div className="queue-chip-row">
      {state ? (
        <span className="queue-chip" style={{ color: ticketStateColor(state) }}>
          {ticketStateLabel(state)}
        </span>
      ) : null}
      {activity ? (
        <span
          className="queue-chip"
          data-activity={activity}
          style={{ color: ticketActivityColor(activity) }}
        >
          {ticketActivityLabel(activity)}
        </span>
      ) : null}
    </div>
  );
}

/** The same two jumps for either half of a lane: the ticket, or its live child. */
function LaneTicketMenu({
  label,
  ticketId,
  runningChild,
}: {
  label: string;
  ticketId: string;
  runningChild?: RunningChildTicket;
}) {
  return (
    <OverflowMenu label={`${label} actions`}>
      <OverflowMenuItem onSelect={() => navigateToTicket(ticketId)}>Go to ticket</OverflowMenuItem>
      {runningChild ? (
        <OverflowMenuItem
          title={`${runningChild.code || runningChild.id} · ${runningChild.title}`}
          onSelect={() => navigateToTicket(runningChild.id)}
        >
          Go to running child
        </OverflowMenuItem>
      ) : null}
    </OverflowMenu>
  );
}

function runningChildFromLabels(
  descendant: TicketHierarchyNode | null | undefined,
  fallback?: RunningChildTicket,
): RunningChildTicket | undefined {
  if (descendant) {
    return { id: descendant.id, title: descendant.title, code: descendant.code };
  }
  return fallback;
}

/**
 * When a queued ticket starts, and how much work it is.
 *
 * Both halves used to be missing, and the second is why the first was wrong:
 * an entry's wait is everything ahead of it in *its own lane*, and each of
 * those is a whole ticket tree rather than a single agent run.
 */
function LaneEntryTiming({ entry }: { entry: LaneEntry }) {
  const wait = formatEstimate(entry.estimated_wait_seconds);
  const duration = formatEstimate(entry.estimated_remaining_seconds);
  const tree = treeSummary(entry.ticket_tree_estimate);

  if (!wait && !duration) return null;

  return (
    <div className="queue-lane-item-timing" data-testid={`lane-entry-${entry.entry_id}-timing`}>
      {wait ? <span>{wait === 'now' ? 'starts next' : `starts in ${wait}`}</span> : null}
      {duration ? <span>{duration} of work</span> : null}
      {tree ? <span>{tree}</span> : null}
    </div>
  );
}

const ATTENTION_LABEL: Record<LaneAttentionEntry['outcome'], string> = {
  blocked: 'Blocked',
  failed: 'Failed',
};

/** "Aug 9, 14:32", or nothing when the entry never recorded an end. */
function formatWhen(entry: LaneAttentionEntry): string {
  const stamp = entry.finished_at ?? entry.started_at;
  if (!stamp) return '';
  const parsed = new Date(stamp);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function ParallelQueueVisualization() {
  const { lanes, stats, estimatedClearSeconds, estimatedWaitSeconds, isWebSocket } =
    useQueueStatus();
  // Seconds, not the rounded minutes the server used to send: an entry that has
  // waited 40 seconds is not "0m", and that was the only figure proving the
  // queue was moving at all.
  const longestWait = stats?.longest_wait_seconds ?? 0;

  const [pendingAdd, setPendingAdd] = useState<QueueAddRequest | null>(null);
  const [laneError, setLaneError] = useState<string | null>(null);
  const [busyEntryId, setBusyEntryId] = useState<string | null>(null);
  const [draggedEntry, setDraggedEntry] = useState<LaneEntry | null>(null);

  const slotUsagePercent = stats?.max_concurrent
    ? ((stats.active_count || 0) / stats.max_concurrent) * 100
    : 0;

  const totalWaiting = useMemo(
    () => (lanes ?? []).reduce((sum, lane) => sum + lane.waiting.length, 0),
    [lanes]
  );

  const runningTicketIds = useMemo(
    () => (lanes ?? []).flatMap((lane) => (lane.running ? [lane.running.ticket_id] : [])),
    [lanes]
  );

  const waitingTicketIds = useMemo(
    () => (lanes ?? []).flatMap((lane) => lane.waiting.map((entry) => entry.ticket_id)),
    [lanes]
  );

  // Every ticket the board already knows about, so the picker cannot offer one
  // that is running or queued somewhere else.
  const claimedTicketIds = useMemo(
    () => [...runningTicketIds, ...waitingTicketIds],
    [runningTicketIds, waitingTicketIds]
  );

  // A running parent can be waiting on a child too, so both halves of a lane
  // ask the same question.
  const runningChildByParent = useRunningChildTickets(claimedTicketIds, runningTicketIds);

  const idle = !(lanes ?? []).some((lane) => lane.running || lane.waiting.length);

  /**
   * Nothing optimistic: the socket reports what the server did, and a lane the
   * server declined to change would be a lie the board could not take back.
   */
  const runLaneAction = async (action: () => Promise<unknown>, entryId: string) => {
    setLaneError(null);
    setBusyEntryId(entryId);
    try {
      await action();
    } catch (error) {
      setLaneError(error instanceof Error ? error.message : 'Lane update failed');
    } finally {
      setBusyEntryId(null);
    }
  };

  const renderRunning = (lane: QueueLane) => {
    const run = lane.running;
    if (!run) return null;
    const live = LIVE_RUN_STATUSES.has(run.status);
    const estimate = run.estimated_duration_seconds;
    // Only a run that is actually executing can have made progress. A parked
    // run has not, and a finished one is not measuring anything at all.
    const progress =
      run.status === 'running' && estimate && estimate > 0
        ? Math.min(100, (run.elapsed_seconds / estimate) * 100)
        : null;
    const remaining = formatEstimate(run.estimated_remaining_seconds);
    const tree = treeSummary(run.ticket_tree_estimate);
    const hierarchy = (
      <TicketHierarchy
        ancestry={run.ticket_ancestry}
        runningDescendant={run.running_descendant}
      />
    );
    const hasHierarchy = (run.ticket_ancestry?.length ?? 0) > 1 || Boolean(run.running_descendant);
    const focusTitle =
      run.running_descendant?.title || run.ticket_title || run.ticket_id;

    return (
      <>
        {hierarchy}
        {/* Hierarchy already ends on the live leaf; only fall back to a lone
            title when there is no path to draw. */}
        {hasHierarchy ? null : (
          <div className="queue-slot-title">{focusTitle}</div>
        )}
        <div className="queue-slot-sub">
          {[run.workspace_name, run.agent_name || run.agent_id, run.stage_key]
            .filter(Boolean)
            .join(' · ')}
        </div>
        <TicketStateChips state={run.ticket_state} activity={run.ticket_activity} />
        <div className="queue-slot-time">
          {live
            ? `${formatDuration(run.elapsed_seconds)} elapsed`
            : // The slot is still occupied, but nothing is working. Saying
              // "elapsed" here is what made a stuck lane look like a busy one.
              `${runStatusLabel(run.status)} · slot held ${formatDuration(run.elapsed_seconds)}`}
          {live && remaining ? (
            <span className="queue-slot-remaining" data-testid={`slot-${lane.slot_number}-remaining`}>
              {' · '}
              {remaining} left
            </span>
          ) : null}
        </div>
        {tree ? (
          <div className="queue-slot-tree" data-testid={`slot-${lane.slot_number}-tree`}>
            {tree}
          </div>
        ) : null}
        {!live ? null : progress === null ? (
          // No median to measure against, or the run is parked on an approval.
          // An indeterminate bar says "live, duration unknown"; a percentage
          // would be made up.
          <div
            className="queue-slot-bar queue-slot-bar--indeterminate"
            data-testid={`slot-${lane.slot_number}-progress-unknown`}
          >
            <div className="queue-slot-bar-fill" />
          </div>
        ) : (
          <div className="queue-slot-bar">
            <div className="queue-slot-bar-fill" style={{ width: `${progress}%` }} />
          </div>
        )}
      </>
    );
  };

  return (
    <div className="queue-panel">
      <div className="queue-panel-head">
        <h2 className="queue-panel-title">Parallel Execution Queue</h2>
        <div className={`queue-conn${isWebSocket ? ' queue-conn--live' : ''}`}>
          <span className="queue-conn-dot" aria-hidden />
          {isWebSocket ? 'Connected' : 'Polling'}
        </div>
      </div>

      <div className="queue-stat-grid">
        <div className="queue-stat">
          <div className="queue-stat-label">Slot usage</div>
          <div className="queue-stat-value">
            {stats?.active_count || 0}/{stats?.max_concurrent || 3}
          </div>
          <div className="queue-stat-bar">
            <div className="queue-stat-bar-fill" style={{ width: `${slotUsagePercent}%` }} />
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Queued</div>
          <div className="queue-stat-value">{totalWaiting}</div>
          <div className="queue-stat-sub">
            {totalWaiting ? 'tickets waiting in lanes' : 'no queue'}
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Est. clear</div>
          <div className="queue-stat-value" data-testid="queue-est-clear">
            {estimatedClearSeconds === null ? '—' : formatDuration(estimatedClearSeconds)}
          </div>
          <div className="queue-stat-sub">
            {estimatedClearSeconds === null
              ? 'no run history yet'
              : // Not "all runs": a lane runs a ticket and its children, and
                // this figure is the whole of that work.
                'every queued ticket done in'}
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Wait time</div>
          {/* Projected, not elapsed. The old figure was how long the oldest
              entry had already sat there, which answered a question nobody
              asked of a queue: what they want is when their ticket starts. */}
          <div className="queue-stat-value" data-testid="queue-wait-time">
            {!totalWaiting
              ? '—'
              : estimatedWaitSeconds === null
                ? '—'
                : formatEstimate(estimatedWaitSeconds)}
          </div>
          <div className="queue-stat-sub">
            {!totalWaiting
              ? 'no queue'
              : estimatedWaitSeconds === null
                ? 'no run history yet'
                : `until the last one starts${
                    longestWait ? ` · waiting ${formatDuration(longestWait)}` : ''
                  }`}
          </div>
        </div>
      </div>

      <div className="queue-section-head">
        <span className="queue-section-title">Execution lanes</span>
        <span className="queue-section-hint">
          Adding runs it, or queues it behind what is already there
        </span>
      </div>

      {laneError ? (
        <div className="queue-inline-error" role="alert">
          <span className="queue-inline-error-dot" aria-hidden />
          <span className="queue-inline-error-message">{laneError}</span>
          <IconCloseButton onClick={() => setLaneError(null)} aria-label="Dismiss error" />
        </div>
      ) : null}

      <div className="queue-slot-grid">
        {(lanes ?? []).map((lane) => (
          <div
            key={`slot-${lane.slot_number}`}
            className={`queue-slot${lane.running ? ' queue-slot--busy' : ''}`}
            data-testid={`slot-${lane.slot_number}`}
            onDragOver={(event) => {
              if (draggedEntry) event.preventDefault();
            }}
            onDrop={() => {
              if (!draggedEntry) return;
              // Dropped on the lane itself: join the back of that lane's queue.
              void runLaneAction(
                () =>
                  queueLanesApi.move(
                    draggedEntry.entry_id,
                    lane.slot_number,
                    lane.waiting.length + 1
                  ),
                draggedEntry.entry_id
              );
              setDraggedEntry(null);
            }}
          >
            <div className="queue-slot-head">
              <span className="queue-slot-dot" aria-hidden />
              <span className="queue-slot-name">Slot {lane.slot_number}</span>
              <span
                className="queue-slot-badge"
                data-run-status={lane.running ? lane.running.status : 'available'}
              >
                {lane.running ? runStatusLabel(lane.running.status) : 'available'}
              </span>
              {lane.running ? (
                <LaneTicketMenu
                  label={
                    lane.running.running_descendant?.code ||
                    lane.running.ticket_code ||
                    lane.running.ticket_title ||
                    lane.running.ticket_id
                  }
                  ticketId={lane.running.ticket_id}
                  runningChild={runningChildFromLabels(
                    lane.running.running_descendant,
                    runningChildByParent.get(lane.running.ticket_id),
                  )}
                />
              ) : null}
            </div>

            <div className="queue-slot-body">
              {lane.running ? (
                renderRunning(lane)
              ) : (
                <>
                  <div className="queue-slot-title">Available</div>
                  <div className="queue-slot-sub">Idle · add a ticket to start one</div>
                </>
              )}

              {lane.attention?.length ? (
                <div
                  className="queue-lane-attention"
                  data-testid={`slot-${lane.slot_number}-attention`}
                >
                  <div className="queue-lane-attention-label">
                    Stopped in this lane ({lane.attention_total ?? lane.attention.length})
                  </div>
                  {lane.attention.map((entry) => (
                    <div
                      key={entry.entry_id}
                      className={`queue-lane-attention-item${
                        busyEntryId === entry.entry_id ? ' is-busy' : ''
                      }`}
                      data-testid={`lane-attention-${entry.entry_id}`}
                    >
                      <span
                        className="queue-lane-attention-badge"
                        data-outcome={entry.outcome}
                      >
                        {ATTENTION_LABEL[entry.outcome] ?? entry.outcome}
                      </span>
                      <div className="queue-lane-attention-copy">
                        <div className="queue-lane-attention-title">
                          {entry.ticket_title || entry.ticket_id}
                        </div>
                        <div className="queue-lane-attention-sub">
                          {[
                            entry.ticket_external_id,
                            entry.workspace_slug || entry.workspace_name,
                            entry.last_stage_key,
                            formatWhen(entry),
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                        {entry.failure_reason ? (
                          <div className="queue-lane-attention-reason" title={entry.failure_reason}>
                            {entry.failure_reason}
                          </div>
                        ) : null}
                      </div>
                      <OverflowMenu label={`${entry.ticket_external_id || entry.ticket_title} actions`}>
                        <OverflowMenuItem onSelect={() => navigateToTicket(entry.ticket_id)}>
                          Go to ticket
                        </OverflowMenuItem>
                        <OverflowMenuItem
                          onSelect={() =>
                            setPendingAdd({
                              ticketId: entry.ticket_id,
                              slotNumber: lane.slot_number,
                              workspaceSlug: entry.workspace_slug,
                              laneIsIdle: !lane.running,
                            })
                          }
                        >
                          Re-queue in this lane
                        </OverflowMenuItem>
                      </OverflowMenu>
                      <IconCloseButton
                        disabled={busyEntryId === entry.entry_id}
                        aria-label={`Dismiss ${entry.ticket_external_id || entry.ticket_title} from slot ${lane.slot_number}`}
                        onClick={() =>
                          void runLaneAction(
                            () => queueLanesApi.dismiss(entry.entry_id),
                            entry.entry_id
                          )
                        }
                      />
                    </div>
                  ))}
                  {(lane.attention_total ?? 0) > lane.attention.length ? (
                    <div className="queue-lane-attention-more">
                      {(lane.attention_total ?? 0) - lane.attention.length} older not shown
                    </div>
                  ) : null}
                </div>
              ) : null}

              {lane.waiting.length ? (
                <div className="queue-lane-queue" data-testid={`slot-${lane.slot_number}-queue`}>
                  <div className="queue-lane-queue-label">
                    Next in this lane ({lane.waiting.length})
                  </div>
                  {lane.waiting.map((entry) => {
                    const runningChild = runningChildFromLabels(
                      entry.running_descendant,
                      runningChildByParent.get(entry.ticket_id),
                    );
                    const entryLabel =
                      entry.running_descendant?.code ||
                      entry.ticket_code ||
                      entry.ticket_title ||
                      entry.ticket_id;
                    const hasHierarchy =
                      (entry.ticket_ancestry?.length ?? 0) > 1 ||
                      Boolean(entry.running_descendant);
                    return (
                      <div
                        key={entry.entry_id}
                        className={`queue-lane-item${
                          busyEntryId === entry.entry_id ? ' is-busy' : ''
                        }`}
                        draggable
                        onDragStart={() => setDraggedEntry(entry)}
                        onDragEnd={() => setDraggedEntry(null)}
                        data-testid={`lane-entry-${entry.entry_id}`}
                      >
                        <span className="queue-lane-item-position">{entry.position}</span>
                        <div className="queue-lane-item-copy">
                          <TicketHierarchy
                            ancestry={entry.ticket_ancestry}
                            runningDescendant={entry.running_descendant}
                          />
                          {hasHierarchy ? null : (
                            <div className="queue-lane-item-title">
                              {entry.ticket_title || entry.ticket_id}
                            </div>
                          )}
                          <div className="queue-lane-item-sub">
                            {[
                              hasHierarchy ? null : entry.ticket_code,
                              entry.workspace_name,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          </div>
                          <TicketStateChips
                            state={entry.ticket_state}
                            activity={entry.ticket_activity}
                          />
                          <LaneEntryTiming entry={entry} />
                        </div>
                        <LaneTicketMenu
                          label={entryLabel}
                          ticketId={entry.ticket_id}
                          runningChild={runningChild}
                        />
                        <IconCloseButton
                          disabled={busyEntryId === entry.entry_id}
                          aria-label={`Remove ${entry.ticket_code || entry.ticket_title} from slot ${lane.slot_number}`}
                          onClick={() =>
                            void runLaneAction(
                              () => queueLanesApi.remove(entry.entry_id),
                              entry.entry_id
                            )
                          }
                        />
                      </div>
                    );
                  })}
                </div>
              ) : null}

              <QueueSlotTicketPicker
                slotNumber={lane.slot_number}
                excludedTicketIds={claimedTicketIds}
                onPick={(ticket) =>
                  setPendingAdd({
                    ticketId: ticket.ticketId,
                    slotNumber: lane.slot_number,
                    workspaceSlug: ticket.workspaceSlug,
                    laneIsIdle: !lane.running,
                  })
                }
              />
            </div>
          </div>
        ))}
      </div>

      {idle ? (
        <div className="queue-idle">All lanes open — add a ticket to one to get started.</div>
      ) : null}

      <QueueAddToLaneModal
        request={pendingAdd}
        onClose={() => setPendingAdd(null)}
        onError={setLaneError}
      />
    </div>
  );
}

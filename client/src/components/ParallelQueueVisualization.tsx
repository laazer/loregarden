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
import type { LaneEntry, QueueLane } from '../lib/queueSocket';
import { navigateToTicket } from '../lib/useAppNavigation';
import './ParallelQueueVisualization.css';

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${minutes}m ${secs}s`;
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

export function ParallelQueueVisualization() {
  const { lanes, stats, estimatedClearSeconds, isWebSocket } = useQueueStatus();

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
    const estimate = run.estimated_duration_seconds;
    const progress =
      estimate && estimate > 0 ? Math.min(100, (run.elapsed_seconds / estimate) * 100) : null;

    return (
      <>
        {/* The id is the fallback, not the label: this is the one place you
            should be able to read what is running. */}
        <div className="queue-slot-title">{run.ticket_title || run.ticket_id}</div>
        <div className="queue-slot-sub">
          {[run.workspace_name, run.agent_name || run.agent_id, run.stage_key]
            .filter(Boolean)
            .join(' · ')}
        </div>
        <div className="queue-slot-time">{formatDuration(run.elapsed_seconds)} elapsed</div>
        {progress === null ? (
          // No median to measure against. An indeterminate bar says "running,
          // duration unknown"; a percentage would be made up.
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
          <div className="queue-stat-value">
            {estimatedClearSeconds === null ? '—' : formatDuration(estimatedClearSeconds)}
          </div>
          <div className="queue-stat-sub">
            {estimatedClearSeconds === null ? 'no run history yet' : 'all runs complete in'}
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Wait time</div>
          <div className="queue-stat-value">{stats?.queue_wait_time_minutes || 0}m</div>
          <div className="queue-stat-sub">
            {stats?.queue_wait_time_minutes ? 'oldest item waiting' : 'no queue'}
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
              <span className="queue-slot-badge">
                {lane.running ? lane.running.status : 'available'}
              </span>
              {lane.running ? (
                <LaneTicketMenu
                  label={
                    lane.running.ticket_code || lane.running.ticket_title || lane.running.ticket_id
                  }
                  ticketId={lane.running.ticket_id}
                  runningChild={runningChildByParent.get(lane.running.ticket_id)}
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

              {lane.waiting.length ? (
                <div className="queue-lane-queue" data-testid={`slot-${lane.slot_number}-queue`}>
                  <div className="queue-lane-queue-label">
                    Next in this lane ({lane.waiting.length})
                  </div>
                  {lane.waiting.map((entry) => {
                    const runningChild = runningChildByParent.get(entry.ticket_id);
                    const entryLabel = entry.ticket_code || entry.ticket_title || entry.ticket_id;
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
                          <div className="queue-lane-item-title">
                            {entry.ticket_title || entry.ticket_id}
                          </div>
                          <div className="queue-lane-item-sub">
                            {[entry.ticket_code, entry.workspace_name].filter(Boolean).join(' · ')}
                          </div>
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

/**
 * The running half of a queue lane, shared by every surface that shows one.
 *
 * The Queue screen draws it inside a lane with that lane's backlog and add
 * controls; an initiative draws it alone, for the tickets of its own that hold
 * a slot. One card, so a running ticket reads the same wherever it appears.
 */

import { OverflowMenu, OverflowMenuItem } from './OverflowMenu';
import type { RunningChildTicket } from '../hooks/useRunningChildTickets';
import type { ActiveRun, TicketHierarchyNode } from '../lib/queueSocket';
import { formatDuration, formatEstimate, treeSummary } from '../lib/queueRunFormat';
import { navigateToTicket } from '../lib/useAppNavigation';
import { progressFillStyle } from '../lib/progressFill';
import { LIVE_RUN_STATUSES } from '../lib/runningTickets';
import {
  repairRouteLabel,
  runStatusLabel,
  ticketActivityChip,
  ticketStateChip,
} from '../lib/ticketStates';
import { StatusChip } from './ui/StatusChip';
import './ParallelQueueVisualization.css';

/**
 * Work-items style path: indented `code · title` rows, leaf highlighted.
 *
 * When a parent holds the lane and a nested child is actually executing, the
 * chain is ancestry plus that descendant so the card names the real work.
 */
export function TicketHierarchy({
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
            {node.title ? <span >{node.title}</span> : null}
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
export function TicketStateChips({
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
        <StatusChip {...ticketStateChip(state)} />
      ) : null}
      {activity ? (
        <StatusChip {...ticketActivityChip(activity)} />
      ) : null}
    </div>
  );
}

/**
 * The two "go to" actions a ticket holding or waiting on a slot offers.
 *
 * Shared because a lane header and a queued entry both offer them, and they are
 * one decision: if "go to running child" changes, it changes in both places.
 */
export function TicketMenuItems({
  ticketId,
  runningChild,
}: {
  ticketId: string;
  runningChild?: RunningChildTicket;
}) {
  return (
    <>
      <OverflowMenuItem onSelect={() => navigateToTicket(ticketId)}>Go to ticket</OverflowMenuItem>
      {runningChild ? (
        <OverflowMenuItem
          title={`${runningChild.code || runningChild.id} · ${runningChild.title}`}
          onSelect={() => navigateToTicket(runningChild.id)}
        >
          Go to running child
        </OverflowMenuItem>
      ) : null}
    </>
  );
}

/** A queued entry's own menu — the ticket's actions, and nothing about lanes. */
export function LaneTicketMenu({
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
      <TicketMenuItems ticketId={ticketId} runningChild={runningChild} />
    </OverflowMenu>
  );
}

/** What a lane's slot card shows while a ticket holds the slot. */
export function RunningSlotBody({ run, slotNumber }: { run: ActiveRun; slotNumber: number }) {
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
        {run.status === 'repairing' ? (
          <span
            className="queue-slot-repair"
            data-testid={`slot-${slotNumber}-repair`}
          >
            {' · '}
            {repairRouteLabel(run.repair_route)}
            {' · attempt '}
            {(run.repair_attempts ?? 0) + 1}
          </span>
        ) : null}
        {live && remaining ? (
          <span className="queue-slot-remaining" data-testid={`slot-${slotNumber}-remaining`}>
            {' · '}
            {remaining} left
          </span>
        ) : null}
      </div>
      {tree ? (
        <div className="queue-slot-tree" data-testid={`slot-${slotNumber}-tree`}>
          {tree}
        </div>
      ) : null}
      {!live ? null : progress === null ? (
        // No median to measure against, or the run is parked on an approval.
        // An indeterminate bar says "live, duration unknown"; a percentage
        // would be made up.
        <div
          className="queue-slot-bar queue-slot-bar--indeterminate"
          data-testid={`slot-${slotNumber}-progress-unknown`}
        >
          <div className="queue-slot-bar-fill" />
        </div>
      ) : (
        <div className="queue-slot-bar">
          <div className="queue-slot-bar-fill" style={progressFillStyle(progress)} />
        </div>
      )}
    </>
  );
}

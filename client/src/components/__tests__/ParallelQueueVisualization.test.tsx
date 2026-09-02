/**
 * The queue's main panel, as lanes.
 *
 * Beyond rendering, the behaviours worth pinning are the ones that replaced
 * invented numbers — slot cards name the ticket rather than its uuid, and a run
 * with no duration history draws an indeterminate bar — plus the lane model
 * itself: each slot shows its own queue, and adding commits rather than stages.
 */

import { act, screen, fireEvent, waitFor, within } from '@testing-library/react';
// Rendered through the shared helper because these surfaces now carry
// `AddToTabMenu`, which lists the operator's tabs and therefore needs a
// QueryClient. Aliased to `render` so every existing call site reads the
// same; the provider is the only thing that changed.
import { renderWithRouter as render } from '../../test/renderWithRouter';
import { ParallelQueueVisualization } from '../ParallelQueueVisualization';
import { api } from '../../api/client';
import { useQueueStatus, type QueueStatusValue } from '../../state/QueueStatusContext';
import { queueLanesApi } from '../../lib/queueLanesApi';
import { navigateToTicket } from '../../lib/useAppNavigation';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));

jest.mock('../../lib/queueLanesApi', () => ({
  queueLanesApi: { add: jest.fn(), remove: jest.fn(), move: jest.fn(), dismiss: jest.fn() },
}));

jest.mock('../../api/client', () => ({
  api: { tickets: jest.fn().mockResolvedValue([]) },
}));

jest.mock('../../lib/useAppNavigation', () => ({
  navigateToTicket: jest.fn(),
}));

const mockTickets = api.tickets as jest.MockedFunction<typeof api.tickets>;

// The picker owns its own ticket query; it has its own test.
jest.mock('../QueueSlotTicketPicker', () => ({
  QueueSlotTicketPicker: ({
    slotNumber,
    onPick,
  }: {
    slotNumber: number;
    onPick: (t: { ticketId: string; workspaceSlug: string }) => void;
  }) => (
    <button
      type="button"
      onClick={() => onPick({ ticketId: `picked-${slotNumber}`, workspaceSlug: 'loregarden' })}
    >
      Add ticket to slot {slotNumber}
    </button>
  ),
}));

// The add dialog fetches the ticket and its stages; it has its own test.
jest.mock('../QueueAddToLaneModal', () => ({
  QueueAddToLaneModal: ({ request }: { request: { slotNumber: number } | null }) =>
    request ? <div data-testid="add-dialog" data-slot={request.slotNumber} /> : null,
}));

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

const runningLane = {
  slot_number: 1,
  running: {
    run_id: 'run-1',
    ticket_id: 'ticket-uuid-1',
    ticket_title: 'Bootstrap vertical slice',
    ticket_code: 'LG-101',
    workspace_name: 'loregarden',
    agent_id: 'backend_implementer',
    agent_name: 'Backend Implementer',
    stage_key: 'apply_patch',
    slot_number: 1,
    elapsed_seconds: 120,
    estimated_duration_seconds: 240,
    status: 'running',
  },
  waiting: [
    {
      entry_id: 'entry-1',
      ticket_id: 'ticket-uuid-2',
      ticket_title: 'Wire the approval gate',
      ticket_code: 'LG-102',
      workspace_id: 'ws-1',
      workspace_name: 'loregarden',
      position: 1,
      auto_approve: false,
      stop_at_stage_key: '',
      queued_at: null,
    },
  ],
};

const baseStatus = {
  activeRuns: [runningLane.running],
  queuedRuns: [],
  lanes: [
    runningLane,
    { slot_number: 2, running: null, waiting: [] },
    { slot_number: 3, running: null, waiting: [] },
  ],
  stats: {
    max_concurrent: 3,
    active_count: 1,
    available_slots: 2,
    queued_count: 1,
    total_slots_occupied: 1,
    longest_wait_seconds: 0,
  },
  estimatedClearSeconds: 480,
  isWebSocket: true,
  loading: false,
  workspaces: [],
  workspacesLoading: false,
  onQueueEvent: jest.fn(() => () => {}),
} as unknown as QueueStatusValue;

const withStatus = (overrides: Record<string, unknown> = {}) => {
  mockUseQueueStatus.mockReturnValue({ ...baseStatus, ...overrides } as QueueStatusValue);
};

beforeEach(() => {
  jest.clearAllMocks();
  // Lane entries look up their running child on mount. Tests that care about
  // the menu resolve this themselves; the rest render against a lookup still
  // in flight, which is the board's first paint anyway.
  mockTickets.mockReturnValue(new Promise(() => {}));
  withStatus();
});

/** An in-progress child of the queued entry, as `/api/tickets` returns it. */
const childTicket = {
  id: 'ticket-uuid-child',
  external_id: 'LG-103',
  title: 'Implement the gate check',
  state: 'in_progress',
  parent_ticket_id: 'ticket-uuid-2',
  workflow_stage_status: 'running',
} as unknown as Awaited<ReturnType<typeof api.tickets>>[number];

describe('rendering', () => {
  test('renders the panel and its lanes', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Parallel Execution Queue')).toBeInTheDocument();
    expect(screen.getByText('Execution lanes')).toBeInTheDocument();
    expect(screen.getByText('Slot 1')).toBeInTheDocument();
    expect(screen.getByText('Slot 3')).toBeInTheDocument();
  });

  test('reports a live socket as connected', () => {
    render(<ParallelQueueVisualization />);
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  test('says polling when the socket is down', () => {
    withStatus({ isWebSocket: false });
    render(<ParallelQueueVisualization />);
    expect(screen.getByText('Polling')).toBeInTheDocument();
  });
});

describe('the running half of a lane', () => {
  test('names the ticket rather than showing its id', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Bootstrap vertical slice')).toBeInTheDocument();
    expect(screen.queryByText('ticket-uuid-1')).not.toBeInTheDocument();
  });

  test('draws the work-items hierarchy when a nested child is live', () => {
    withStatus({
      lanes: [
        {
          ...runningLane,
          running: {
            ...runningLane.running,
            ticket_ancestry: [
              {
                id: 'ticket-uuid-root',
                code: 'M-1',
                title: 'Milestone',
                work_item_type: 'milestone',
              },
              {
                id: 'ticket-uuid-1',
                code: 'LG-101',
                title: 'Bootstrap vertical slice',
                work_item_type: 'feature',
              },
            ],
            running_descendant: {
              id: 'ticket-uuid-leaf',
              code: 'LG-199',
              title: 'Apply the patch',
              work_item_type: 'task',
            },
          },
        },
        { slot_number: 2, running: null, waiting: [] },
        { slot_number: 3, running: null, waiting: [] },
      ],
    });

    render(<ParallelQueueVisualization />);

    const hierarchy = screen.getByLabelText('Ticket hierarchy');
    expect(hierarchy).toHaveTextContent('M-1');
    expect(hierarchy).toHaveTextContent('Milestone');
    expect(hierarchy).toHaveTextContent('LG-101');
    expect(hierarchy).toHaveTextContent('LG-199');
    expect(hierarchy).toHaveTextContent('Apply the patch');
  });

  test('leads the subtitle with the workspace', () => {
    // Lanes are shared across workspaces, so whose work this is comes first.
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('loregarden · Backend Implementer · apply_patch')).toBeInTheDocument();
  });

  test('draws progress against the estimated duration', () => {
    const { container } = render(<ParallelQueueVisualization />);

    const fill = container.querySelector('.queue-slot-bar-fill') as HTMLElement;
    expect(fill.style.width).toBe('50%'); // 120s of an estimated 240s
  });

  test('draws an indeterminate bar when the duration is unknown', () => {
    withStatus({
      lanes: [
        { ...runningLane, running: { ...runningLane.running, estimated_duration_seconds: null } },
      ],
    });

    const { container } = render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-1-progress-unknown')).toBeInTheDocument();
    const fill = container.querySelector('.queue-slot-bar-fill') as HTMLElement;
    // No claimed percentage — the sweep animation carries the width.
    expect(fill.style.width).toBe('');
  });

  test('an idle lane says so', () => {
    render(<ParallelQueueVisualization />);
    expect(screen.getAllByText('Available')).toHaveLength(2);
  });

  test('shows the ticket state and activity, not just the slot', () => {
    // The lane knows who holds the slot; these say what the ticket itself is.
    withStatus({
      lanes: [
        {
          ...runningLane,
          running: { ...runningLane.running, ticket_state: 'in_progress', ticket_activity: 'running' },
        },
      ],
    });

    render(<ParallelQueueVisualization />);
    const slot = screen.getByTestId('slot-1');

    expect(within(slot).getByText('In Progress')).toBeInTheDocument();
    expect(within(slot).getAllByText('Running').length).toBeGreaterThan(0);
  });

  test('spells the run status rather than leaking the enum', () => {
    withStatus({
      lanes: [{ ...runningLane, running: { ...runningLane.running, status: 'awaiting_permission' } }],
    });

    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Awaiting approval')).toBeInTheDocument();
    expect(screen.queryByText('awaiting_permission')).not.toBeInTheDocument();
  });

  test('a parked run draws no progress percentage', () => {
    // It is holding a slot, not making headway; a bar would claim otherwise.
    withStatus({
      lanes: [{ ...runningLane, running: { ...runningLane.running, status: 'awaiting_permission' } }],
    });

    const { container } = render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-1-progress-unknown')).toBeInTheDocument();
    const fill = container.querySelector('.queue-slot-bar-fill') as HTMLElement;
    expect(fill.style.width).toBe('');
  });

  test('a slot held by a finished run stops pretending to be busy', () => {
    // The slot-leak case: occupied, but nothing is working behind it.
    withStatus({
      lanes: [
        {
          ...runningLane,
          running: { ...runningLane.running, status: 'succeeded', ticket_activity: 'idle' },
        },
      ],
    });

    const { container } = render(<ParallelQueueVisualization />);

    expect(screen.getByText(/Succeeded · slot held/)).toBeInTheDocument();
    expect(screen.queryByText(/elapsed/)).not.toBeInTheDocument();
    expect(container.querySelector('.queue-slot-bar')).toBeNull();
  });
});

describe('per-lane queues', () => {
  test('a lane shows what is queued behind it', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-1-queue')).toBeInTheDocument();
    expect(screen.getByText('Wire the approval gate')).toBeInTheDocument();
    expect(screen.getByText('Next in this lane (1)')).toBeInTheDocument();
  });

  test('a waiting entry shows why its ticket may not move', () => {
    withStatus({
      lanes: [
        {
          ...runningLane,
          waiting: [
            { ...runningLane.waiting[0], ticket_state: 'blocked', ticket_activity: 'queued' },
          ],
        },
      ],
    });

    render(<ParallelQueueVisualization />);
    const entry = screen.getByTestId('lane-entry-entry-1');

    expect(within(entry).getByText('Blocked')).toBeInTheDocument();
    expect(within(entry).getByText('Queued')).toBeInTheDocument();
  });

  test('an empty lane shows no queue at all', () => {
    render(<ParallelQueueVisualization />);

    // A "0 waiting" affordance on every idle lane would be noise.
    expect(screen.queryByTestId('slot-2-queue')).not.toBeInTheDocument();
    expect(screen.queryByTestId('slot-3-queue')).not.toBeInTheDocument();
  });

  test('a waiting entry can be taken out of its lane', async () => {
    (queueLanesApi.remove as jest.Mock).mockResolvedValue({ status: 'removed' });

    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByLabelText('Remove LG-102 from slot 1'));

    await waitFor(() => expect(queueLanesApi.remove).toHaveBeenCalledWith('entry-1'));
  });

  test('a failed lane change is surfaced rather than swallowed', async () => {
    (queueLanesApi.remove as jest.Mock).mockRejectedValue(new Error('Entry already started'));

    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByLabelText('Remove LG-102 from slot 1'));

    expect(await screen.findByText('Entry already started')).toBeInTheDocument();
  });

  test('dragging an entry onto another lane moves it there', async () => {
    (queueLanesApi.move as jest.Mock).mockResolvedValue({ status: 'moved' });

    render(<ParallelQueueVisualization />);
    fireEvent.dragStart(screen.getByTestId('lane-entry-entry-1'));
    fireEvent.dragOver(screen.getByTestId('slot-2'));
    fireEvent.drop(screen.getByTestId('slot-2'));

    // Joins the back of the target lane — position 1, since it is empty.
    await waitFor(() => expect(queueLanesApi.move).toHaveBeenCalledWith('entry-1', 2, 1));
  });
});

/** Renders the board, settles the child lookup, then opens one card's menu. */
const openLaneMenu = async (trigger: string, inProgress: unknown[] = []) => {
  mockTickets.mockResolvedValue(inProgress as Awaited<ReturnType<typeof api.tickets>>);
  render(<ParallelQueueVisualization />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: trigger }));
};

describe('a queued entry’s overflow menu', () => {
  const openMenu = async (inProgress: unknown[] = []) =>
    openLaneMenu('LG-102 actions', inProgress);

  test('goes to the queued ticket', async () => {
    await openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Go to ticket' }));

    expect(navigateToTicket).toHaveBeenCalledWith('ticket-uuid-2');
  });

  test('offers no running child when the ticket has none', async () => {
    await openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Go to running child' })).not.toBeInTheDocument();
  });

  test('goes to the running child when one exists', async () => {
    await openMenu([childTicket]);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Go to running child' }));

    expect(navigateToTicket).toHaveBeenCalledWith('ticket-uuid-child');
  });

  test('ignores a child that is not running', async () => {
    await openMenu([{ ...childTicket, workflow_stage_status: 'awaiting' }]);

    expect(screen.queryByRole('menuitem', { name: 'Go to running child' })).not.toBeInTheDocument();
  });
});

describe('a running card’s overflow menu', () => {
  /** A child of the ticket running in slot 1, itself running in slot 2. */
  const runningChildOfRunner = {
    ...childTicket,
    id: 'ticket-uuid-3',
    external_id: 'LG-104',
    parent_ticket_id: 'ticket-uuid-1',
  };

  test('goes to the running ticket', async () => {
    await openLaneMenu('Lane 1 actions');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Go to ticket' }));

    expect(navigateToTicket).toHaveBeenCalledWith('ticket-uuid-1');
  });

  test('goes to the running child when one exists', async () => {
    await openLaneMenu('Lane 1 actions', [runningChildOfRunner]);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Go to running child' }));

    expect(navigateToTicket).toHaveBeenCalledWith('ticket-uuid-3');
  });

  test('offers no running child when the ticket has none', async () => {
    await openLaneMenu('Lane 1 actions');

    expect(screen.queryByRole('menuitem', { name: 'Go to running child' })).not.toBeInTheDocument();
  });

  test('an idle lane still has one, holding what applies to a lane', async () => {
    // This used to assert an idle lane had *no* menu, which was right while the
    // menu was the running ticket's: no ticket, nothing to act on. The header's
    // menu is the lane's now — an idle lane is still a lane you may want to
    // watch in a tab — so the rule became "one menu, holding what applies".
    render(<ParallelQueueVisualization />);
    await act(async () => {});

    const head = screen.getByTestId('slot-2').querySelector('.queue-slot-head') as HTMLElement;
    const triggers = within(head).queryAllByRole('button', { name: /actions/ });
    expect(triggers).toHaveLength(1);

    fireEvent.click(triggers[0]);
    expect(screen.queryByRole('menuitem', { name: 'Go to ticket' })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'New tab' })).toBeInTheDocument();
  });

  test('a running lane header carries one menu, not two', async () => {
    // The defect this replaces: "add to a tab" arrived as a second trigger
    // beside the ticket's, so one header had two `⋯` and no way to tell which
    // held what.
    render(<ParallelQueueVisualization />);
    await act(async () => {});

    // Scoped to the *header*, not the slot: each queued entry below it has a
    // menu of its own, which is correct — one menu per thing, and an entry is
    // its own thing.
    const head = screen.getByTestId('slot-1').querySelector('.queue-slot-head') as HTMLElement;
    expect(within(head).queryAllByRole('button', { name: /actions|to a tab/ })).toHaveLength(1);
  });
});

describe('adding to a lane', () => {
  test('picking a ticket opens the dialog rather than committing', () => {
    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByText('Add ticket to slot 2'));

    expect(screen.getByTestId('add-dialog')).toHaveAttribute('data-slot', '2');
    // Adding is committing, so it goes through the dialog first.
    expect(queueLanesApi.add).not.toHaveBeenCalled();
  });

  test('every lane can be added to, busy or not', () => {
    render(<ParallelQueueVisualization />);

    // Adding to a busy lane queues behind it — that is the whole point.
    expect(screen.getByText('Add ticket to slot 1')).toBeInTheDocument();
    expect(screen.getByText('Add ticket to slot 2')).toBeInTheDocument();
  });
});

describe('empty state', () => {
  test('invites a first ticket when every lane is idle', () => {
    withStatus({
      lanes: [
        { slot_number: 1, running: null, waiting: [] },
        { slot_number: 2, running: null, waiting: [] },
      ],
      activeRuns: [],
    });

    render(<ParallelQueueVisualization />);

    expect(screen.getByText(/All lanes open/)).toBeInTheDocument();
  });

  test('is absent while anything is running or queued', () => {
    render(<ParallelQueueVisualization />);
    expect(screen.queryByText(/All lanes open/)).not.toBeInTheDocument();
  });
});

describe('estimates', () => {
  /** A lane holding a parent with two unfinished children behind it. */
  const treeLane = {
    ...runningLane,
    running: {
      ...runningLane.running,
      orchestration_run_id: 'orch-1',
      elapsed_seconds: 600,
      estimated_duration_seconds: 5400,
      estimated_remaining_seconds: 4800,
      ticket_tree_estimate: {
        work_seconds: 9000,
        critical_path_seconds: 4800,
        projected_seconds: 4800,
        ticket_count: 3,
        unknown_tickets: 0,
        stage_count: 9,
      },
    },
    waiting: [
      {
        ...runningLane.waiting[0],
        estimated_wait_seconds: 4800,
        estimated_remaining_seconds: 3600,
        ticket_tree_estimate: {
          work_seconds: 3600,
          critical_path_seconds: 3600,
          projected_seconds: 3600,
          ticket_count: 2,
          unknown_tickets: 1,
          stage_count: 4,
        },
      },
    ],
  };

  test('a running lane says how much of its ticket tree is left', () => {
    withStatus({ lanes: [treeLane], activeRuns: [treeLane.running] });
    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-1-remaining')).toHaveTextContent('~1h 20m left');
    // The count is why the estimate is an hour and not four minutes.
    expect(screen.getByTestId('slot-1-tree')).toHaveTextContent('3 tickets · 9 stages left');
  });

  test('a running lane draws a real bar once its whole span is known', () => {
    withStatus({ lanes: [treeLane], activeRuns: [treeLane.running] });
    render(<ParallelQueueVisualization />);

    // The indeterminate bar was every lane's permanent state, because no single
    // agent median could describe a whole pipeline.
    expect(screen.queryByTestId('slot-1-progress-unknown')).not.toBeInTheDocument();
  });

  test('a queued entry says when it starts, not just that it is queued', () => {
    withStatus({ lanes: [treeLane], activeRuns: [treeLane.running] });
    render(<ParallelQueueVisualization />);

    const timing = screen.getByTestId('lane-entry-entry-1-timing');
    expect(timing).toHaveTextContent('starts in ~1h 20m');
    expect(timing).toHaveTextContent('~1h of work');
    // The part of the subtree with no history is admitted rather than dropped.
    expect(timing).toHaveTextContent('1 unestimated');
  });

  test('the wait tile projects forward and reports the observed wait beside it', () => {
    withStatus({
      lanes: [treeLane],
      activeRuns: [treeLane.running],
      estimatedWaitSeconds: 4800,
      stats: { ...baseStatus.stats, longest_wait_seconds: 45 },
    });
    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('queue-wait-time')).toHaveTextContent('~1h 20m');
    // 45 seconds used to render as "0m", on the one figure that was supposed to
    // show the queue moving.
    expect(screen.getByText(/waiting 45s/)).toBeInTheDocument();
  });

  test('no history says so rather than showing a zero', () => {
    withStatus({
      lanes: [
        {
          ...treeLane,
          running: { ...treeLane.running, estimated_remaining_seconds: null },
          waiting: [
            {
              ...runningLane.waiting[0],
              estimated_wait_seconds: null,
              estimated_remaining_seconds: null,
            },
          ],
        },
      ],
      estimatedClearSeconds: null,
      estimatedWaitSeconds: null,
    });
    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('queue-est-clear')).toHaveTextContent('—');
    expect(screen.getByTestId('queue-wait-time')).toHaveTextContent('—');
    expect(screen.queryByTestId('lane-entry-entry-1-timing')).not.toBeInTheDocument();
  });
});

describe('what stopped in a lane', () => {
  /** A lane whose last ticket blocked and released the slot. */
  const stoppedCard = {
    entry_id: 'entry-stopped',
    ticket_id: 'ticket-uuid-9',
    ticket_external_id: 'LG-109',
    ticket_title: 'Persist the creature definition',
    ticket_state: 'blocked',
    workspace_id: 'ws-1',
    workspace_slug: 'loregarden',
    workspace_name: 'loregarden',
    slot_number: 2,
    outcome: 'blocked' as const,
    run_code: 'orch-9',
    last_stage_key: 'test_design',
    failure_reason: 'No workflow template for this ticket',
    retry_count: 0,
    started_at: null,
    finished_at: null,
    duration_seconds: null,
  };

  const withAttention = (attention = [stoppedCard], attentionTotal = 1) =>
    withStatus({
      lanes: [
        runningLane,
        {
          slot_number: 2,
          running: null,
          waiting: [],
          attention,
          attention_total: attentionTotal,
        },
        { slot_number: 3, running: null, waiting: [] },
      ],
    });

  test('an idle lane still shows the ticket that stopped in it', () => {
    withAttention();
    render(<ParallelQueueVisualization />);

    const section = screen.getByTestId('slot-2-attention');
    // Without this the lane read "Available" — indistinguishable from one that
    // has never run anything.
    expect(section).toHaveTextContent('Stopped in this lane (1)');
    expect(section).toHaveTextContent('Blocked');
    expect(section).toHaveTextContent('Persist the creature definition');
    expect(section).toHaveTextContent('No workflow template for this ticket');
  });

  test('lanes with nothing to answer for show no section', () => {
    withAttention();
    render(<ParallelQueueVisualization />);

    expect(screen.queryByTestId('slot-1-attention')).not.toBeInTheDocument();
    expect(screen.queryByTestId('slot-3-attention')).not.toBeInTheDocument();
  });

  test('dismissing tells the server, since a reload must not clear it', async () => {
    (queueLanesApi.dismiss as jest.Mock).mockResolvedValue({ status: 'dismissed' });
    withAttention();
    render(<ParallelQueueVisualization />);

    fireEvent.click(screen.getByRole('button', { name: /Dismiss LG-109/ }));

    await waitFor(() => expect(queueLanesApi.dismiss).toHaveBeenCalledWith('entry-stopped'));
  });

  test('a failed dismissal surfaces rather than looking like it worked', async () => {
    (queueLanesApi.dismiss as jest.Mock).mockRejectedValue(new Error('Entry is live'));
    withAttention();
    render(<ParallelQueueVisualization />);

    fireEvent.click(screen.getByRole('button', { name: /Dismiss LG-109/ }));

    expect(await screen.findByText('Entry is live')).toBeInTheDocument();
  });

  test('re-queueing opens the add dialog for that same lane', async () => {
    withAttention();
    await openLaneMenu('LG-109 actions');

    fireEvent.click(screen.getByRole('menuitem', { name: 'Re-queue in this lane' }));

    expect(screen.getByTestId('add-dialog')).toHaveAttribute('data-slot', '2');
  });

  test('the menu still jumps to the stopped ticket', async () => {
    withAttention();
    await openLaneMenu('LG-109 actions');

    fireEvent.click(screen.getByRole('menuitem', { name: 'Go to ticket' }));

    expect(navigateToTicket).toHaveBeenCalledWith('ticket-uuid-9');
  });

  test('the count is the true one, and the tail is admitted', () => {
    withAttention([stoppedCard], 4);
    render(<ParallelQueueVisualization />);

    const section = screen.getByTestId('slot-2-attention');
    expect(section).toHaveTextContent('Stopped in this lane (4)');
    expect(section).toHaveTextContent('3 older not shown');
  });
});

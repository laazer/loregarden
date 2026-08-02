/**
 * The queue's main panel, as lanes.
 *
 * Beyond rendering, the behaviours worth pinning are the ones that replaced
 * invented numbers — slot cards name the ticket rather than its uuid, and a run
 * with no duration history draws an indeterminate bar — plus the lane model
 * itself: each slot shows its own queue, and adding commits rather than stages.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ParallelQueueVisualization } from '../ParallelQueueVisualization';
import { useQueueStatus, type QueueStatusValue } from '../../state/QueueStatusContext';
import { queueLanesApi } from '../../lib/queueLanesApi';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));

jest.mock('../../lib/queueLanesApi', () => ({
  queueLanesApi: { add: jest.fn(), remove: jest.fn(), move: jest.fn() },
}));

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
    queue_wait_time_minutes: 2,
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
  withStatus();
});

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
});

describe('per-lane queues', () => {
  test('a lane shows what is queued behind it', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-1-queue')).toBeInTheDocument();
    expect(screen.getByText('Wire the approval gate')).toBeInTheDocument();
    expect(screen.getByText('Next in this lane (1)')).toBeInTheDocument();
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

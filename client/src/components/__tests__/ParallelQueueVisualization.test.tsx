/**
 * The queue's main panel.
 *
 * Beyond rendering, two behaviours are worth pinning because they replaced
 * invented numbers: slot cards must show the ticket's title rather than its
 * uuid, and a run with no duration history must draw an indeterminate bar
 * instead of a percentage.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ParallelQueueVisualization } from '../ParallelQueueVisualization';
import { useQueueStatus, type QueueStatusValue } from '../../state/QueueStatusContext';
import { useQueueStagingStore } from '../../state/queueStagingStore';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));

// The slot picker owns its own ticket query; it has its own test.
jest.mock('../QueueSlotTicketPicker', () => ({
  QueueSlotTicketPicker: ({ slotNumber }: { slotNumber: number }) => (
    <button type="button">Add ticket to slot {slotNumber}</button>
  ),
}));

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

const baseStatus: QueueStatusValue = {
  activeRuns: [
    {
      run_id: 'run-1',
      ticket_id: 'ticket-uuid-1',
      ticket_title: 'Bootstrap vertical slice',
      ticket_code: 'LG-101',
      agent_id: 'backend_implementer',
      agent_name: 'Backend Implementer',
      stage_key: 'apply_patch',
      slot_number: 1,
      elapsed_seconds: 120,
      estimated_duration_seconds: 240,
      status: 'running',
    },
  ],
  queuedRuns: [
    {
      run_id: 'run-2',
      ticket_id: 'ticket-uuid-2',
      ticket_title: 'Wire the approval gate',
      agent_id: 'test_designer',
      agent_name: 'Test Designer',
      stage_key: 'write_tests',
      position: 1,
      wait_seconds: 150,
      estimated_start_at: new Date('2026-07-30T10:00:00Z').toISOString(),
    },
    {
      run_id: 'run-3',
      ticket_id: 'ticket-uuid-3',
      ticket_title: 'Backfill the ledger',
      agent_id: 'test_designer',
      agent_name: 'Test Designer',
      stage_key: 'write_tests',
      position: 2,
      wait_seconds: 450,
      estimated_start_at: new Date('2026-07-30T10:05:00Z').toISOString(),
    },
  ],
  stats: {
    max_concurrent: 3,
    active_count: 1,
    available_slots: 2,
    queued_count: 2,
    total_slots_occupied: 1,
    queue_wait_time_minutes: 2,
  },
  estimatedClearSeconds: 480,
  isWebSocket: true,
  loading: false,
  workspaces: [],
  workspacesLoading: false,
  onQueueEvent: jest.fn(() => () => {}),
};

const withStatus = (overrides: Partial<QueueStatusValue> = {}) => {
  mockUseQueueStatus.mockReturnValue({ ...baseStatus, ...overrides });
};

beforeEach(() => {
  jest.clearAllMocks();
  useQueueStagingStore.setState({ staged: {} });
  withStatus();
});

describe('rendering', () => {
  test('renders the panel and its sections', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Parallel Execution Queue')).toBeInTheDocument();
    expect(screen.getByText('Execution slots')).toBeInTheDocument();
    expect(screen.getByText('Waiting')).toBeInTheDocument();
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

describe('stat tiles', () => {
  test('shows slot usage', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Slot usage')).toBeInTheDocument();
    expect(screen.getByText('1/3')).toBeInTheDocument();
  });

  test('shows the projected clear time from the server', () => {
    render(<ParallelQueueVisualization />);

    // 480s, formatted — not a per-run constant multiplied by the queue depth.
    expect(screen.getByText('8m 0s')).toBeInTheDocument();
    expect(screen.getByText('all runs complete in')).toBeInTheDocument();
  });

  test('says so when there is no history to project from', () => {
    withStatus({ estimatedClearSeconds: null });

    render(<ParallelQueueVisualization />);

    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText('no run history yet')).toBeInTheDocument();
  });

  test('shows the queue wait time', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Wait time')).toBeInTheDocument();
    expect(screen.getByText('2m')).toBeInTheDocument();
  });
});

describe('execution slots', () => {
  test('renders one card per configured slot', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Slot 1')).toBeInTheDocument();
    expect(screen.getByText('Slot 2')).toBeInTheDocument();
    expect(screen.getByText('Slot 3')).toBeInTheDocument();
  });

  test('names the ticket rather than showing its id', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Bootstrap vertical slice')).toBeInTheDocument();
    expect(screen.queryByText('ticket-uuid-1')).not.toBeInTheDocument();
  });

  test('falls back to the id when the server sent no title', () => {
    withStatus({
      activeRuns: [{ ...baseStatus.activeRuns[0], ticket_title: '' }],
    });

    render(<ParallelQueueVisualization />);

    expect(screen.getByText('ticket-uuid-1')).toBeInTheDocument();
  });

  test('subtitles the slot with the agent and stage', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Backend Implementer · apply_patch')).toBeInTheDocument();
  });

  test('draws progress against the estimated duration', () => {
    const { container } = render(<ParallelQueueVisualization />);

    const fill = container.querySelector('.queue-slot-bar-fill') as HTMLElement;
    // 120s elapsed of an estimated 240s.
    expect(fill.style.width).toBe('50%');
  });

  test('draws an indeterminate bar when the duration is unknown', () => {
    withStatus({
      activeRuns: [{ ...baseStatus.activeRuns[0], estimated_duration_seconds: null }],
    });

    const { container } = render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-1-progress-unknown')).toBeInTheDocument();
    const fill = container.querySelector('.queue-slot-bar-fill') as HTMLElement;
    // No claimed percentage — the sweep animation carries the width.
    expect(fill.style.width).toBe('');
  });

  test('marks unoccupied slots available', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getAllByText('available')).toHaveLength(2);
    expect(screen.getAllByText('Available')).toHaveLength(2);
  });
});

describe('slot staging', () => {
  const stage = (slotNumber: number, ticketId = 'ticket-uuid-9') =>
    useQueueStagingStore.getState().stage(slotNumber, {
      ticketId,
      code: 'LG-9',
      title: 'Rework the approval bridge',
      workspaceName: 'loregarden',
    });

  test('offers a picker on every open slot, and none on a busy one', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Add ticket to slot 2')).toBeInTheDocument();
    expect(screen.getByText('Add ticket to slot 3')).toBeInTheDocument();
    // Slot 1 is running.
    expect(screen.queryByText('Add ticket to slot 1')).not.toBeInTheDocument();
  });

  test('a staged ticket names itself in the slot it was put in', () => {
    stage(3);

    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Rework the approval bridge')).toBeInTheDocument();
    expect(screen.getByTestId('slot-3')).toHaveTextContent('LG-9');
    // Staging is not starting.
    expect(screen.getByTestId('slot-3-start')).toBeInTheDocument();
    expect(screen.getByTestId('slot-2')).toHaveTextContent('Available');
  });

  test('starting asks for the slot the card is drawn in', async () => {
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock as unknown as typeof fetch;
    stage(3);

    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByTestId('slot-3-start'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/parallel/runs/ticket-uuid-9');
    expect(url).toContain('slot_number=3');
    expect(init.method).toBe('POST');
  });

  test('a started ticket stops being staged', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({}) }) as unknown as typeof fetch;
    stage(3);

    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByTestId('slot-3-start'));

    await waitFor(() =>
      expect(useQueueStagingStore.getState().staged[3]).toBeUndefined(),
    );
  });

  test('a refused start keeps the ticket staged and says why', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Ticket is blocked' }),
    }) as unknown as typeof fetch;
    stage(3);

    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByTestId('slot-3-start'));

    expect(await screen.findByText('Ticket is blocked')).toBeInTheDocument();
    expect(useQueueStagingStore.getState().staged[3]).toBeDefined();
  });

  test('start-all is offered once per staged slot and only while some are staged', () => {
    const { rerender } = render(<ParallelQueueVisualization />);
    expect(screen.queryByTestId('start-all-staged')).not.toBeInTheDocument();

    stage(2, 'ticket-uuid-8');
    stage(3, 'ticket-uuid-9');
    rerender(<ParallelQueueVisualization />);

    expect(screen.getByTestId('start-all-staged')).toHaveTextContent('Start 2 staged');
  });

  test('drops a staged ticket the server has already put to work', () => {
    // Staged in slot 2, but the snapshot now reports it running in slot 2.
    stage(2, 'ticket-uuid-2');
    withStatus({
      activeRuns: [
        { ...baseStatus.activeRuns[0], slot_number: 2, ticket_id: 'ticket-uuid-2' },
      ],
    });

    render(<ParallelQueueVisualization />);

    expect(useQueueStagingStore.getState().staged[2]).toBeUndefined();
    expect(screen.queryByTestId('slot-2-start')).not.toBeInTheDocument();
  });

  test('drops a staged ticket that got queued from elsewhere', () => {
    stage(3, 'ticket-uuid-3');

    render(<ParallelQueueVisualization />);

    // ticket-uuid-3 is in the waiting list of the base snapshot.
    expect(useQueueStagingStore.getState().staged[3]).toBeUndefined();
  });

  test('staging a ticket again moves it rather than duplicating it', () => {
    stage(2, 'ticket-uuid-9');
    stage(3, 'ticket-uuid-9');

    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('slot-3-start')).toBeInTheDocument();
    expect(screen.queryByTestId('slot-2-start')).not.toBeInTheDocument();
  });

  test('a staged ticket can be taken back out of its slot', () => {
    stage(3);

    render(<ParallelQueueVisualization />);
    fireEvent.click(screen.getByLabelText('Remove LG-9 from slot 3'));

    expect(screen.queryByTestId('slot-3-start')).not.toBeInTheDocument();
    expect(screen.getByText('Add ticket to slot 3')).toBeInTheDocument();
  });
});

describe('waiting list', () => {
  test('names each queued ticket', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('Wire the approval gate')).toBeInTheDocument();
    expect(screen.getByText('Backfill the ledger')).toBeInTheDocument();
  });

  test('numbers the queue', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByTestId('queue-item-1')).toBeInTheDocument();
    expect(screen.getByTestId('queue-item-2')).toBeInTheDocument();
  });

  test('shows how long each run has waited', () => {
    render(<ParallelQueueVisualization />);

    expect(screen.getByText('waited 2m 30s')).toBeInTheDocument();
    expect(screen.getByText('waited 7m 30s')).toBeInTheDocument();
  });

  test('is absent when nothing is queued', () => {
    withStatus({ queuedRuns: [] });

    render(<ParallelQueueVisualization />);

    expect(screen.queryByText('Waiting')).not.toBeInTheDocument();
  });
});

describe('drag to reorder', () => {
  test('rows are draggable', () => {
    const { container } = render(<ParallelQueueVisualization />);

    container.querySelectorAll('.queue-waiting-row').forEach((row) => {
      expect(row).toHaveAttribute('draggable', 'true');
    });
  });

  test('marks the dragged row and clears it on drag end', () => {
    const { container } = render(<ParallelQueueVisualization />);

    const row = container.querySelector('.queue-waiting-row') as HTMLElement;
    fireEvent.dragStart(row);
    expect(row.classList.contains('is-dragging')).toBe(true);

    fireEvent.dragEnd(row);
    expect(row.classList.contains('is-dragging')).toBe(false);
  });

  test('marks the row being dragged over', () => {
    const { container } = render(<ParallelQueueVisualization />);

    const rows = container.querySelectorAll('.queue-waiting-row');
    fireEvent.dragStart(rows[0]);
    fireEvent.dragOver(rows[1]);

    expect((rows[1] as HTMLElement).classList.contains('is-drop-target')).toBe(true);
  });
});

describe('empty state', () => {
  test('invites a dispatch when nothing is running or queued', () => {
    withStatus({ activeRuns: [], queuedRuns: [] });

    render(<ParallelQueueVisualization />);

    expect(screen.getByText(/All slots open/)).toBeInTheDocument();
  });
});

describe('live updates', () => {
  test('re-renders from new context data', () => {
    const { rerender } = render(<ParallelQueueVisualization />);

    expect(screen.getByText('1/3')).toBeInTheDocument();

    withStatus({ stats: { ...baseStatus.stats, active_count: 3 } });
    rerender(<ParallelQueueVisualization />);

    expect(screen.getByText('3/3')).toBeInTheDocument();
  });
});

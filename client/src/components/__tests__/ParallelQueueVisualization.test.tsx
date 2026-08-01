/**
 * The queue's main panel.
 *
 * Beyond rendering, two behaviours are worth pinning because they replaced
 * invented numbers: slot cards must show the ticket's title rather than its
 * uuid, and a run with no duration history must draw an indeterminate bar
 * instead of a percentage.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { ParallelQueueVisualization } from '../ParallelQueueVisualization';
import { useQueueStatus, type QueueStatusValue } from '../../state/QueueStatusContext';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));

// The dispatch picker owns its own query and endpoint; it has its own test.
jest.mock('../QueueDispatchButton', () => ({
  QueueDispatchButton: () => <button type="button">Dispatch run</button>,
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
  workspace: null,
  workspaces: [],
  workspacesLoading: false,
  activeSlug: 'loregarden',
  setWorkspaceSlug: jest.fn(),
  onQueueEvent: jest.fn(() => () => {}),
};

const withStatus = (overrides: Partial<QueueStatusValue> = {}) => {
  mockUseQueueStatus.mockReturnValue({ ...baseStatus, ...overrides });
};

beforeEach(() => {
  jest.clearAllMocks();
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

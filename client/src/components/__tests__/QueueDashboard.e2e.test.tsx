/**
 * The queue dashboard shell: main column, side rail, and the toast stack.
 *
 * The header metrics this file used to assert now live in the top action bar
 * (QueueTopbarControls), so they are covered there instead. What is new here
 * is the toast wiring — QueueNotifications had no producer at all until the
 * socket started forwarding events, so the mapping from event to toast is the
 * thing worth pinning.
 */

import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { QueueDashboard } from '../QueueDashboard';
import { useQueueStatus, type QueueStatusValue } from '../../state/QueueStatusContext';
import { useToastStore } from '../../state/toastStore';
import type { QueueEvent } from '../../lib/queueSocket';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));
jest.mock('../ParallelQueueVisualization', () => ({
  ParallelQueueVisualization: () => <div data-testid="queue-visualization" />,
}));
jest.mock('../QueueAdvancedControls', () => ({
  QueueAdvancedControls: () => <div data-testid="queue-controls" />,
}));
// Owns its own query and endpoint; covered by QueueGitAutomation.test.tsx.
jest.mock('../QueueGitAutomation', () => ({
  QueueGitAutomation: () => <div data-testid="queue-git-automation" />,
}));
jest.mock('../QueueHistoricalAnalytics', () => ({
  QueueHistoricalAnalytics: () => <div data-testid="queue-analytics" />,
}));

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

/** Captures the listener the dashboard registers, so tests can fire events. */
let emit: (event: QueueEvent) => void;

const baseStatus: QueueStatusValue = {
  activeRuns: [
    {
      run_id: 'run-1',
      ticket_id: 'ticket-1',
      ticket_title: 'Bootstrap vertical slice',
      agent_id: 'backend_implementer',
      agent_name: 'Backend Implementer',
      slot_number: 1,
      elapsed_seconds: 120,
      status: 'running',
    },
  ],
  queuedRuns: [
    {
      run_id: 'run-2',
      ticket_id: 'ticket-2',
      agent_id: 'test_designer',
      position: 1,
      wait_seconds: 300,
      estimated_start_at: new Date('2026-07-30T10:00:00Z').toISOString(),
    },
    {
      run_id: 'run-3',
      ticket_id: 'ticket-3',
      agent_id: 'test_designer',
      position: 2,
      wait_seconds: 600,
      estimated_start_at: new Date('2026-07-30T10:05:00Z').toISOString(),
    },
  ],
  stats: {
    max_concurrent: 3,
    active_count: 1,
    available_slots: 2,
    queued_count: 2,
    total_slots_occupied: 1,
    queue_wait_time_minutes: 5,
  },
  estimatedClearSeconds: 300,
  isWebSocket: true,
  loading: false,
  workspace: null,
  workspaces: [],
  workspacesLoading: false,
  activeSlug: 'loregarden',
  setWorkspaceSlug: jest.fn(),
  onQueueEvent: jest.fn(),
};

const withStatus = (overrides: Partial<QueueStatusValue> = {}) => {
  mockUseQueueStatus.mockReturnValue({
    ...baseStatus,
    onQueueEvent: (listener) => {
      emit = listener;
      return () => {};
    },
    ...overrides,
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  withStatus();
});

/** What the queue pushed into the app-wide stack. */
const toasts = () => useToastStore.getState().toasts;

describe('layout', () => {
  test('renders the main panel and the side rail', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    expect(screen.getByTestId('queue-visualization')).toBeInTheDocument();
    expect(screen.getByText('Queue status')).toBeInTheDocument();
  });

  test('does not render a page header — that lives in the topbar now', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    expect(screen.queryByText('Queue Dashboard')).not.toBeInTheDocument();
  });
});

describe('tabs', () => {
  test('renders every tab', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    expect(screen.getByText('Overview')).toBeInTheDocument();
    expect(screen.getByText('Review')).toBeInTheDocument();
    expect(screen.getByText('Controls')).toBeInTheDocument();
    expect(screen.getByText('Analytics')).toBeInTheDocument();
  });

  test('switching to controls marks it active and mounts the panel', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    const tab = screen.getByText('Controls');
    fireEvent.click(tab);

    expect(tab).toHaveClass('is-active');
    expect(screen.getByTestId('queue-controls')).toBeInTheDocument();
  });

  test('switching to analytics marks it active and mounts the panel', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    const tab = screen.getByText('Analytics');
    fireEvent.click(tab);

    expect(tab).toHaveClass('is-active');
    expect(screen.getByTestId('queue-analytics')).toBeInTheDocument();
  });

  test('hides controls when showControls is false', () => {
    render(<QueueDashboard workspaceId="ws-1" showControls={false} />);

    expect(screen.queryByText('Controls')).not.toBeInTheDocument();
  });

  test('hides analytics when showAnalytics is false', () => {
    render(<QueueDashboard workspaceId="ws-1" showAnalytics={false} />);

    expect(screen.queryByText('Analytics')).not.toBeInTheDocument();
  });
});

describe('overview tiles', () => {
  test('shows the four queue-status tiles', () => {
    const { container } = render(<QueueDashboard workspaceId="ws-1" />);

    const grid = within(container.querySelector('.queue-rail-grid')!);
    expect(grid.getByText('Total runs')).toBeInTheDocument();
    expect(grid.getByText('Utilization')).toBeInTheDocument();
    expect(grid.getByText('Active slots')).toBeInTheDocument();
    expect(grid.getByText('Queue depth')).toBeInTheDocument();
  });

  test('computes utilization and slot usage from stats', () => {
    const { container } = render(<QueueDashboard workspaceId="ws-1" />);

    const grid = within(container.querySelector('.queue-rail-grid')!);
    expect(grid.getByText('33%')).toBeInTheDocument();
    expect(grid.getByText('1/3')).toBeInTheDocument();
  });

  test('follows the context when it changes', () => {
    const { container, rerender } = render(<QueueDashboard workspaceId="ws-1" />);

    expect(within(container.querySelector('.queue-rail-grid')!).getByText('1/3'))
      .toBeInTheDocument();

    withStatus({ stats: { ...baseStatus.stats, active_count: 2 } });
    rerender(<QueueDashboard workspaceId="ws-1" />);

    expect(within(container.querySelector('.queue-rail-grid')!).getByText('2/3'))
      .toBeInTheDocument();
  });
});

describe('queue event toasts', () => {
  test('a completed run raises a success toast', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    act(() => {
      emit({
        type: 'run_completed',
        timestamp: '2026-07-30T10:00:00Z',
        data: { runId: 'run-abc12345', status: 'succeeded' },
      });
    });

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0]).toMatchObject({ tone: 'success', title: 'Run complete' });
    expect(toasts()[0].message).toMatch(/run-abc/);
  });

  test('a failed run raises an error toast, not a success one', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    act(() => {
      emit({
        type: 'run_completed',
        timestamp: '2026-07-30T10:00:00Z',
        data: { runId: 'run-abc12345', status: 'failed' },
      });
    });

    expect(toasts()[0]).toMatchObject({ tone: 'error', title: 'Run failed' });
  });

  test('a promotion raises an info toast naming the slot', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    act(() => {
      emit({
        type: 'queue_promoted',
        timestamp: '2026-07-30T10:00:00Z',
        data: { runId: 'run-abc12345', slotNumber: 2 },
      });
    });

    expect(toasts()[0]).toMatchObject({ tone: 'info', title: 'Run promoted' });
    expect(toasts()[0].message).toMatch(/slot 2/);
  });

  test('an error event carries the server message', () => {
    render(<QueueDashboard workspaceId="ws-1" />);

    act(() => {
      emit({
        type: 'error',
        timestamp: '2026-07-30T10:00:00Z',
        data: { runId: 'run-1', message: 'Failed to create run: no slots' },
      });
    });

    expect(toasts()[0]).toMatchObject({
      tone: 'error',
      title: 'Queue error',
      message: 'Failed to create run: no slots',
      // Stays until dismissed.
      duration: 0,
    });
  });
});

describe('resilience', () => {
  test('renders with empty queue data rather than throwing', () => {
    withStatus({
      activeRuns: [],
      queuedRuns: [],
      estimatedClearSeconds: null,
      isWebSocket: false,
    });

    expect(() => render(<QueueDashboard workspaceId="ws-1" />)).not.toThrow();
    expect(screen.getByText(/All slots open/)).toBeInTheDocument();
  });
});

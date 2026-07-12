/**
 * Unit tests for ParallelExecutionTimeline component.
 */

import { render, screen, within } from '@testing-library/react';
import { ParallelExecutionTimeline } from '../ParallelExecutionTimeline';

jest.mock('../../hooks/useParallelExecution', () => ({
  useParallelExecution: jest.fn(),
}));

import { useParallelExecution } from '../../hooks/useParallelExecution';

const mockUseParallelExecution = useParallelExecution as jest.MockedFunction<
  typeof useParallelExecution
>;

describe('ParallelExecutionTimeline', () => {
  const mockStats = {
    max_concurrent: 3,
    active_count: 2,
    available_slots: 1,
    queued_count: 1,
    total_slots_occupied: 2,
    queue_wait_time_minutes: 5,
  };

  const mockActiveRuns = [
    {
      run_id: 'run-1',
      ticket_id: 'feature-123',
      slot_number: 1,
      elapsed_seconds: 120,
      status: 'running',
      agent_id: 'planner',
    },
    {
      run_id: 'run-2',
      ticket_id: 'feature-456',
      slot_number: 2,
      elapsed_seconds: 45,
      status: 'running',
      agent_id: 'implementer',
    },
  ];

  const mockQueuedRuns = [
    {
      run_id: 'run-3',
      ticket_id: 'feature-789',
      position: 1,
      estimated_start_at: new Date(Date.now() + 300000).toISOString(),
      wait_seconds: 300,
      agent_id: 'reviewer',
    },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders timeline with active and queued runs', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: mockQueuedRuns,
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText('Execution Timeline')).toBeInTheDocument();
    expect(screen.getByTestId('timeline-slot-1')).toBeInTheDocument();
    expect(screen.getByTestId('timeline-slot-2')).toBeInTheDocument();
    expect(screen.getByTestId('timeline-slot-3')).toBeInTheDocument();
  });

  test('renders all slot numbers based on max_concurrent', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText('Slot 1')).toBeInTheDocument();
    expect(screen.getByText('Slot 2')).toBeInTheDocument();
    expect(screen.getByText('Slot 3')).toBeInTheDocument();
  });

  test('displays loading state when loading', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: true,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText('Loading timeline...')).toBeInTheDocument();
  });

  test('displays error message', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: 'Failed to fetch timeline data',
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByTestId('timeline-error')).toBeInTheDocument();
    expect(screen.getByText(/Failed to fetch timeline data/)).toBeInTheDocument();
  });

  test('shows legend items for Running, Queued, and Available', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: mockQueuedRuns,
      stats: mockStats,
      loading: false,
      error: null,
    });

    const { container } = render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    const legend = within(container.querySelector('.timeline-legend')!);
    expect(legend.getByText('Running')).toBeInTheDocument();
    expect(legend.getByText('Queued')).toBeInTheDocument();
    expect(legend.getByText('Available')).toBeInTheDocument();
  });

  test('renders timeline bars for active runs', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByTestId('timeline-bar-run-1')).toBeInTheDocument();
    expect(screen.getByTestId('timeline-bar-run-2')).toBeInTheDocument();
  });

  test('renders timeline bars for queued runs', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: mockQueuedRuns,
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByTestId('timeline-bar-run-3')).toBeInTheDocument();
  });

  test('displays empty slot message when slot has no runs', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [mockActiveRuns[0]],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    const emptySlots = screen.getAllByText('Available');
    expect(emptySlots.length).toBeGreaterThan(0);
  });

  test('shows timeline scale with correct labels', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    // Timeline should show 0s and 10m (600s)
    expect(screen.getByText('0s')).toBeInTheDocument();
    expect(screen.getByText('10m')).toBeInTheDocument();
  });

  test('displays estimated completion time', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: mockQueuedRuns,
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText(/Est\. Completion:/)).toBeInTheDocument();
    expect(screen.getByText(/Queue Wait:/)).toBeInTheDocument();
  });

  test('displays queue wait time from stats', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: mockQueuedRuns,
      stats: { ...mockStats, queue_wait_time_minutes: 5 },
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText('~5m')).toBeInTheDocument();
  });

  test('displays "None" for queue wait when zero', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: { ...mockStats, queue_wait_time_minutes: 0 },
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText('None')).toBeInTheDocument();
  });

  test('marks active slots with indicator', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [mockActiveRuns[0]],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(
      <ParallelExecutionTimeline workspaceId="workspace-1" />
    );

    const slot1 = screen.getByTestId('timeline-slot-1');
    const indicator = slot1.querySelector('.slot-active-indicator');
    expect(indicator).toBeInTheDocument();
  });

  test('hides active indicator for inactive slots', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [mockActiveRuns[0]],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(
      <ParallelExecutionTimeline workspaceId="workspace-1" />
    );

    const slot2 = screen.getByTestId('timeline-slot-2');
    const indicator = slot2.querySelector('.slot-active-indicator');
    expect(indicator).not.toBeInTheDocument();
  });

  test('renders timeline bar with ticket ID label', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText('feature-123')).toBeInTheDocument();
    expect(screen.getByText('feature-456')).toBeInTheDocument();
  });

  test('passes correct workspaceId to hook', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="custom-workspace" />);

    expect(mockUseParallelExecution).toHaveBeenCalledWith('custom-workspace', 5000);
  });

  test('uses custom maxDuration prop', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(
      <ParallelExecutionTimeline workspaceId="workspace-1" maxDuration={1200} />
    );

    expect(screen.getByText('20m')).toBeInTheDocument();
  });

  test('renders info section only when there are active or queued runs', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    const { rerender } = render(
      <ParallelExecutionTimeline workspaceId="workspace-1" />
    );

    expect(screen.queryByText(/Est\. Completion:/)).not.toBeInTheDocument();

    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    rerender(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByText(/Est\. Completion:/)).toBeInTheDocument();
  });

  test('calculates timeline bar position based on elapsed seconds', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(
      <ParallelExecutionTimeline workspaceId="workspace-1" />
    );

    const bar1 = screen.getByTestId('timeline-bar-run-1');
    const bar2 = screen.getByTestId('timeline-bar-run-2');

    // Bar 1 should be further along (120s) than Bar 2 (45s)
    const style1 = window.getComputedStyle(bar1);
    const style2 = window.getComputedStyle(bar2);

    expect(style1.left).not.toEqual(style2.left);
  });

  test('handles zero elapsed seconds', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [
        {
          ...mockActiveRuns[0],
          elapsed_seconds: 0,
        },
      ],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelExecutionTimeline workspaceId="workspace-1" />);

    expect(screen.getByTestId('timeline-bar-run-1')).toBeInTheDocument();
  });
});

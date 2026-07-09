/**
 * Unit tests for ParallelFeatureCards component.
 */

import { render, screen } from '@testing-library/react';
import { ParallelFeatureCards } from '../ParallelFeatureCards';

// Mock the useParallelExecution hook
jest.mock('../../hooks/useParallelExecution', () => ({
  useParallelExecution: jest.fn(),
}));

import { useParallelExecution } from '../../hooks/useParallelExecution';

const mockUseParallelExecution = useParallelExecution as jest.MockedFunction<
  typeof useParallelExecution
>;

describe('ParallelFeatureCards', () => {
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

  test('renders with active and queued runs', async () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: mockQueuedRuns,
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    // Check stats bar
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('2/3')).toBeInTheDocument();
    expect(screen.getByText('Queue')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();

    // Check active runs section
    expect(screen.getByText(/Active Features/)).toBeInTheDocument();
    expect(screen.getByTestId('active-run-run-1')).toBeInTheDocument();
    expect(screen.getByTestId('active-run-run-2')).toBeInTheDocument();

    // Check queued runs section
    expect(screen.getByText(/Queue/)).toBeInTheDocument();
    expect(screen.getByTestId('queued-run-run-3')).toBeInTheDocument();
  });

  test('displays loading state', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: true,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    expect(screen.getByText(/Loading parallel execution/)).toBeInTheDocument();
  });

  test('displays error message', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: 'Failed to fetch status',
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    expect(screen.getByTestId('parallel-error')).toBeInTheDocument();
    expect(screen.getByText(/Failed to fetch status/)).toBeInTheDocument();
  });

  test('displays empty state when no runs', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: {
        ...mockStats,
        active_count: 0,
        available_slots: 3,
        queued_count: 0,
      },
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    expect(screen.getByText(/No parallel runs active/)).toBeInTheDocument();
    expect(screen.getByText(/All slots available/)).toBeInTheDocument();
  });

  test('displays only active runs when no queue', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: {
        ...mockStats,
        queued_count: 0,
      },
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    expect(screen.getByText(/Active Features/)).toBeInTheDocument();
    expect(screen.queryByText(/Queue/)).not.toBeInTheDocument();
  });

  test('displays only queued runs when none active', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: mockQueuedRuns,
      stats: {
        ...mockStats,
        active_count: 0,
      },
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    expect(screen.getByText(/Queue/)).toBeInTheDocument();
    expect(screen.queryByText(/Active Features/)).not.toBeInTheDocument();
  });

  test('formats elapsed time correctly', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [
        {
          ...mockActiveRuns[0],
          elapsed_seconds: 125, // 2m 5s
        },
      ],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    expect(screen.getByText(/2m 5s/)).toBeInTheDocument();
  });

  test('passes correct workspaceId to hook', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="custom-workspace" />);

    expect(mockUseParallelExecution).toHaveBeenCalledWith('custom-workspace', 5000);
  });

  test('renders with compact mode', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: [],
      stats: mockStats,
      loading: false,
      error: null,
    });

    const { container } = render(
      <ParallelFeatureCards workspaceId="workspace-1" compact={true} />
    );

    expect(container).toBeInTheDocument();
  });

  test('displays agent information', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: mockActiveRuns,
      queuedRuns: mockQueuedRuns,
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    // Check agent names appear
    expect(screen.getByText('planner')).toBeInTheDocument();
    expect(screen.getByText('implementer')).toBeInTheDocument();
    expect(screen.getByText('reviewer')).toBeInTheDocument();
  });

  test('displays queue position numbers', () => {
    mockUseParallelExecution.mockReturnValue({
      activeRuns: [],
      queuedRuns: [
        { ...mockQueuedRuns[0], position: 1 },
        {
          run_id: 'run-4',
          ticket_id: 'feature-xyz',
          position: 2,
          estimated_start_at: new Date(Date.now() + 600000).toISOString(),
          wait_seconds: 600,
          agent_id: 'tester',
        },
      ],
      stats: mockStats,
      loading: false,
      error: null,
    });

    render(<ParallelFeatureCards workspaceId="workspace-1" />);

    // Check position numbers appear (these are inside .position-number)
    const queueItems = screen.getAllByTestId(/queued-run-/);
    expect(queueItems).toHaveLength(2);
  });
});

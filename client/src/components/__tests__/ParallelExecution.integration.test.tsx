/**
 * Integration tests for parallel execution components working together.
 * Tests ParallelFeatureCards, ParallelExecutionTimeline, and WorktreeConflictWarning.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { ParallelFeatureCards } from '../ParallelFeatureCards';
import { ParallelExecutionTimeline } from '../ParallelExecutionTimeline';
import { WorktreeConflictWarning } from '../WorktreeConflictWarning';

jest.mock('../../hooks/useParallelExecution', () => ({
  useParallelExecution: jest.fn(),
}));

jest.mock('../../hooks/useWorktreeConflicts', () => ({
  useWorktreeConflicts: jest.fn(),
}));

import { useParallelExecution } from '../../hooks/useParallelExecution';
import { useWorktreeConflicts } from '../../hooks/useWorktreeConflicts';

const mockUseParallelExecution = useParallelExecution as jest.MockedFunction<
  typeof useParallelExecution
>;

const mockUseWorktreeConflicts = useWorktreeConflicts as jest.MockedFunction<
  typeof useWorktreeConflicts
>;

describe('Parallel Execution Integration Tests', () => {
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

  const mockConflicts = [
    {
      path: 'src/app.ts',
      type: 'code' as const,
      conflictLines: 12,
      auto_mergeable: false,
      resolution_suggestion: 'Merge both implementations',
    },
  ];

  const mockConflictPreview = {
    conflicting_files: mockConflicts,
    total_conflicts: 1,
    auto_mergeable_count: 0,
    severity: 'medium' as const,
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('Parallel Execution Dashboard', () => {
    test('displays complete execution status with cards and timeline', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: mockQueuedRuns,
        stats: mockStats,
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: null,
      });

      const { container } = render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <ParallelExecutionTimeline workspaceId="workspace-1" />
        </>
      );

      // Check ParallelFeatureCards content
      expect(screen.getByText('Active')).toBeInTheDocument();
      expect(screen.getByText('2/3')).toBeInTheDocument();
      expect(screen.getByText(/Active Features/)).toBeInTheDocument();

      // Check ParallelExecutionTimeline content
      expect(screen.getByText('Execution Timeline')).toBeInTheDocument();
      expect(screen.getByText('Running')).toBeInTheDocument();
      expect(screen.getByText('Queued')).toBeInTheDocument();

      expect(container).toBeInTheDocument();
    });

    test('updates both components when execution state changes', async () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: [],
        stats: { ...mockStats, queued_count: 0 },
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: null,
      });

      const { rerender } = render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <ParallelExecutionTimeline workspaceId="workspace-1" />
        </>
      );

      // Initially shows queue section
      expect(screen.queryByText(/Queue/)).not.toBeInTheDocument();

      // Update to show queue
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: mockQueuedRuns,
        stats: mockStats,
        loading: false,
        error: null,
      });

      rerender(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <ParallelExecutionTimeline workspaceId="workspace-1" />
        </>
      );

      expect(screen.getByText(/Queue/)).toBeInTheDocument();
    });

    test('displays conflicts alongside execution status', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: [],
        stats: mockStats,
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: mockConflicts,
        preview: mockConflictPreview,
        details: {
          worktree_id: 'wt-1',
          run_id: 'run-1',
          conflicts: mockConflicts,
          merge_preview: mockConflictPreview,
          timestamp: new Date().toISOString(),
        },
        hasConflicts: true,
        loading: false,
        error: null,
      });

      render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <WorktreeConflictWarning worktreeId="wt-1" />
        </>
      );

      expect(screen.getByText('Merge Conflicts')).toBeInTheDocument();
      expect(screen.getByText('1 conflicts')).toBeInTheDocument();
      expect(screen.getByText('src/app.ts')).toBeInTheDocument();
    });
  });

  describe('Concurrent Component Updates', () => {
    test('timeline and cards stay in sync during active run updates', async () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: [mockActiveRuns[0]],
        queuedRuns: mockQueuedRuns,
        stats: { ...mockStats, active_count: 1, available_slots: 2 },
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: null,
      });

      const { rerender } = render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <ParallelExecutionTimeline workspaceId="workspace-1" />
        </>
      );

      // Check initial state
      expect(screen.getByText('1/3')).toBeInTheDocument();

      // Simulate run completion - promote from queue
      mockUseParallelExecution.mockReturnValue({
        activeRuns: [
          ...mockActiveRuns.slice(0, 1),
          { ...mockQueuedRuns[0], slot_number: 2, elapsed_seconds: 0, status: 'running' },
        ],
        queuedRuns: [],
        stats: { ...mockStats, active_count: 2, available_slots: 1, queued_count: 0 },
        loading: false,
        error: null,
      });

      rerender(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <ParallelExecutionTimeline workspaceId="workspace-1" />
        </>
      );

      expect(screen.getByText('2/3')).toBeInTheDocument();
      expect(screen.queryByText(/Queue/)).not.toBeInTheDocument();
    });

    test('conflict warning appears during active merge attempt', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: [],
        stats: mockStats,
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: mockConflicts,
        preview: mockConflictPreview,
        details: {
          worktree_id: 'wt-1',
          run_id: 'run-1',
          conflicts: mockConflicts,
          merge_preview: mockConflictPreview,
          timestamp: new Date().toISOString(),
        },
        hasConflicts: true,
        loading: false,
        error: null,
      });

      const onResolve = jest.fn();
      const onAbort = jest.fn();

      render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <WorktreeConflictWarning
            worktreeId="wt-1"
            onResolve={onResolve}
            onAbort={onAbort}
          />
        </>
      );

      const resolveButton = screen.getByText('Resolve Conflicts');
      const abortButton = screen.getByText('Abort');

      expect(resolveButton).toBeInTheDocument();
      expect(abortButton).toBeInTheDocument();

      fireEvent.click(resolveButton);
      expect(onResolve).toHaveBeenCalled();
    });
  });

  describe('Error Handling Across Components', () => {
    test('handles execution API error while conflicts load', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: [],
        queuedRuns: [],
        stats: mockStats,
        loading: false,
        error: 'Failed to fetch parallel status',
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: true,
        error: null,
      });

      render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <WorktreeConflictWarning worktreeId="wt-1" />
        </>
      );

      expect(screen.getByText(/Failed to fetch parallel status/)).toBeInTheDocument();
    });

    test('displays conflict error alongside execution cards', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: [],
        stats: mockStats,
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: 'Failed to detect conflicts',
      });

      render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <WorktreeConflictWarning worktreeId="wt-1" />
        </>
      );

      expect(screen.getByText(/Active Features/)).toBeInTheDocument();
      expect(screen.getByText(/Failed to detect conflicts/)).toBeInTheDocument();
    });
  });

  describe('Loading States Across Components', () => {
    test('shows loading indicators while fetching execution and conflict data', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: [],
        queuedRuns: [],
        stats: mockStats,
        loading: true,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: true,
        error: null,
      });

      render(
        <>
          <ParallelFeatureCards workspaceId="workspace-1" />
          <WorktreeConflictWarning worktreeId="wt-1" />
        </>
      );

      expect(screen.getByText(/Loading parallel execution/)).toBeInTheDocument();
      expect(screen.getByText(/Checking for merge conflicts/)).toBeInTheDocument();
    });
  });

  describe('Empty States and Transitions', () => {
    test('shows empty state when all slots are available', () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: [],
        queuedRuns: [],
        stats: {
          max_concurrent: 3,
          active_count: 0,
          available_slots: 3,
          queued_count: 0,
          total_slots_occupied: 0,
          queue_wait_time_minutes: 0,
        },
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: null,
      });

      render(
        <ParallelFeatureCards workspaceId="workspace-1" />
      );

      expect(screen.getByText(/No parallel runs active/)).toBeInTheDocument();
      expect(screen.getByText(/All slots available/)).toBeInTheDocument();
    });

    test('transitions from empty to active execution', async () => {
      mockUseParallelExecution.mockReturnValue({
        activeRuns: [],
        queuedRuns: [],
        stats: {
          max_concurrent: 3,
          active_count: 0,
          available_slots: 3,
          queued_count: 0,
          total_slots_occupied: 0,
          queue_wait_time_minutes: 0,
        },
        loading: false,
        error: null,
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: null,
      });

      const { rerender } = render(
        <ParallelFeatureCards workspaceId="workspace-1" />
      );

      expect(screen.getByText(/No parallel runs active/)).toBeInTheDocument();

      // Transition to active execution
      mockUseParallelExecution.mockReturnValue({
        activeRuns: mockActiveRuns,
        queuedRuns: [],
        stats: mockStats,
        loading: false,
        error: null,
      });

      rerender(<ParallelFeatureCards workspaceId="workspace-1" />);

      expect(screen.queryByText(/No parallel runs active/)).not.toBeInTheDocument();
      expect(screen.getByText(/Active Features/)).toBeInTheDocument();
    });
  });

  describe('Real-time Updates Simulation', () => {
    test('simulates parallel execution lifecycle', async () => {
      let callCount = 0;

      mockUseParallelExecution.mockImplementation(() => {
        const states = [
          // Initial: empty
          {
            activeRuns: [],
            queuedRuns: [],
            stats: {
              max_concurrent: 3,
              active_count: 0,
              available_slots: 3,
              queued_count: 0,
              total_slots_occupied: 0,
              queue_wait_time_minutes: 0,
            },
            loading: false,
            error: null,
          },
          // State 2: one run active
          {
            activeRuns: [mockActiveRuns[0]],
            queuedRuns: mockQueuedRuns,
            stats: {
              ...mockStats,
              active_count: 1,
              available_slots: 2,
              queued_count: 1,
            },
            loading: false,
            error: null,
          },
          // State 3: two runs active
          {
            activeRuns: mockActiveRuns,
            queuedRuns: [],
            stats: {
              ...mockStats,
              active_count: 2,
              available_slots: 1,
              queued_count: 0,
            },
            loading: false,
            error: null,
          },
        ];
        return states[Math.min(callCount++, states.length - 1)];
      });

      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [],
        preview: null,
        details: null,
        hasConflicts: false,
        loading: false,
        error: null,
      });

      const { rerender } = render(
        <ParallelFeatureCards workspaceId="workspace-1" />
      );

      // Check empty state
      expect(screen.getByText(/No parallel runs active/)).toBeInTheDocument();

      // Simulate updates
      rerender(<ParallelFeatureCards workspaceId="workspace-1" />);
      expect(screen.getByText('1/3')).toBeInTheDocument();

      rerender(<ParallelFeatureCards workspaceId="workspace-1" />);
      expect(screen.getByText('2/3')).toBeInTheDocument();
    });
  });
});

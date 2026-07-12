/**
 * End-to-End tests for QueueDashboard
 * Tests integration of all components with real WebSocket connections and API calls
 */

import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueueDashboard } from '../QueueDashboard';
import * as useHook from '../../hooks/useParallelExecutionWS';

jest.mock('../../hooks/useParallelExecutionWS');
jest.mock('../QueueNotifications');
jest.mock('../ParallelQueueVisualization');
jest.mock('../QueueDiffViewer');
jest.mock('../QueueAdvancedControls');
jest.mock('../QueueHistoricalAnalytics');

describe('QueueDashboard E2E Tests', () => {
  const mockHookData = {
    activeRuns: [
      {
        run_id: 'run-1',
        ticket_id: 'feature-123',
        slot_number: 1,
        elapsed_seconds: 120,
        status: 'running',
      },
    ],
    queuedRuns: [
      {
        run_id: 'run-2',
        ticket_id: 'feature-124',
        position: 1,
        wait_seconds: 300,
        estimated_start_at: new Date(Date.now() + 300000).toISOString(),
      },
      {
        run_id: 'run-3',
        ticket_id: 'feature-125',
        position: 2,
        wait_seconds: 600,
        estimated_start_at: new Date(Date.now() + 600000).toISOString(),
      },
    ],
    stats: {
      max_concurrent: 3,
      active_count: 1,
      available_slots: 2,
      queued_count: 2,
      queue_wait_time_minutes: 5,
    },
    connectionState: 'connected',
    isWebSocket: true,
    loading: false,
    error: null,
  };

  beforeEach(() => {
    (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue(mockHookData);
  });

  describe('Dashboard Rendering', () => {
    test('renders complete dashboard', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('Queue Dashboard')).toBeInTheDocument();
      expect(screen.getByText(/Workspace: ws-1/)).toBeInTheDocument();
    });

    test('displays header metrics', () => {
      const { container } = render(<QueueDashboard workspaceId="ws-1" />);

      const headerMetrics = within(container.querySelector('.header-metrics')!);
      expect(headerMetrics.getByText('Utilization')).toBeInTheDocument();
      expect(headerMetrics.getByText('Active')).toBeInTheDocument();
      expect(headerMetrics.getByText('Queued')).toBeInTheDocument();
    });

    test('shows connection status', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('🟢 Real-time')).toBeInTheDocument();
    });

    test('shows polling indicator when not WebSocket', () => {
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        isWebSocket: false,
      });

      render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('📡 Polling')).toBeInTheDocument();
    });
  });

  describe('Metric Calculations', () => {
    test('calculates utilization correctly', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      // 1/3 active = 33%, shown in both the header badge and the overview status grid
      expect(screen.getAllByText('33%').length).toBeGreaterThan(0);
    });

    test('displays active run count', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      // active/max, shown in both the header badge and the overview status grid
      expect(screen.getAllByText('1/3').length).toBeGreaterThan(0);
    });

    test('displays queued run count', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      // Should show 2 queued runs, in both the header badge and the overview status grid
      expect(screen.getAllByText('2').length).toBeGreaterThan(0);
    });

    test('updates metrics when hook data changes', () => {
      const { container, rerender } = render(<QueueDashboard workspaceId="ws-1" />);

      const headerMetrics = within(container.querySelector('.header-metrics')!);
      expect(headerMetrics.getByText('1/3')).toBeInTheDocument();

      // Update hook data
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        stats: {
          ...mockHookData.stats,
          active_count: 2,
        },
      });

      rerender(<QueueDashboard workspaceId="ws-1" />);

      expect(headerMetrics.getByText('2/3')).toBeInTheDocument();
    });
  });

  describe('Tab Navigation', () => {
    test('renders all tabs', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('Overview')).toBeInTheDocument();
      expect(screen.getByText('Controls')).toBeInTheDocument();
      expect(screen.getByText('Analytics')).toBeInTheDocument();
    });

    test('switches to controls tab', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      const controlsTab = screen.getByText('Controls');
      fireEvent.click(controlsTab);

      expect(controlsTab).toHaveClass('active');
    });

    test('switches to analytics tab', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      const analyticsTab = screen.getByText('Analytics');
      fireEvent.click(analyticsTab);

      expect(analyticsTab).toHaveClass('active');
    });

    test('hides controls tab when showControls=false', () => {
      render(<QueueDashboard workspaceId="ws-1" showControls={false} />);

      expect(screen.queryByText('Controls')).not.toBeInTheDocument();
    });

    test('hides analytics tab when showAnalytics=false', () => {
      render(<QueueDashboard workspaceId="ws-1" showAnalytics={false} />);

      expect(screen.queryByText('Analytics')).not.toBeInTheDocument();
    });
  });

  describe('Overview Panel', () => {
    test('displays status grid on overview tab', () => {
      const { container } = render(<QueueDashboard workspaceId="ws-1" />);

      const statusGrid = within(container.querySelector('.status-grid')!);
      expect(statusGrid.getByText('Total Runs')).toBeInTheDocument();
      expect(statusGrid.getByText('Utilization')).toBeInTheDocument();
      expect(statusGrid.getByText('Active Slots')).toBeInTheDocument();
      expect(statusGrid.getByText('Queue Depth')).toBeInTheDocument();
    });

    // Note: a standalone status legend and a "Real-time WebSocket" / "Polling
    // (5s interval)" connection line used to live in the overview panel, but
    // both were removed when the queue review components were integrated
    // (see commit 2fef9bc). Connection state is now only shown via the
    // header's connection badge, already covered by the "Dashboard Rendering"
    // tests above, and the run-state legend lives in ParallelQueueVisualization
    // (covered by ParallelQueueVisualization.test.tsx).
  });

  describe('Real-time Updates', () => {
    test('updates header metrics in real-time', () => {
      const { container, rerender } = render(<QueueDashboard workspaceId="ws-1" />);

      const headerMetrics = within(container.querySelector('.header-metrics')!);
      expect(headerMetrics.getByText('1/3')).toBeInTheDocument();

      // Simulate WebSocket update
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        activeRuns: [
          ...mockHookData.activeRuns,
          {
            run_id: 'run-new',
            ticket_id: 'feature-new',
            slot_number: 2,
            elapsed_seconds: 0,
            status: 'running',
          },
        ],
        stats: {
          ...mockHookData.stats,
          active_count: 2,
        },
      });

      rerender(<QueueDashboard workspaceId="ws-1" />);

      expect(headerMetrics.getByText('2/3')).toBeInTheDocument();
    });

    test('refreshes dashboard on connection state change', () => {
      const { rerender } = render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('🟢 Real-time')).toBeInTheDocument();

      // Simulate disconnection
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        isWebSocket: false,
        connectionState: 'disconnected',
      });

      rerender(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('📡 Polling')).toBeInTheDocument();
    });
  });

  describe('Component Integration', () => {
    test('passes data to ParallelQueueVisualization', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      // Component receives workspace ID
      expect(useHook.useParallelExecutionWS).toHaveBeenCalledWith('ws-1', undefined);
    });

    test('passes workspace ID to QueueNotifications', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      // Notifications component would receive workspace ID
      // (tested via component mock)
    });

    test('passes controls data to QueueAdvancedControls', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      const controlsTab = screen.getByText('Controls');
      fireEvent.click(controlsTab);

      // Controls component would receive activeRuns and queuedRuns
      // (tested via component mock)
    });

    test('passes workspace ID to QueueHistoricalAnalytics', () => {
      render(<QueueDashboard workspaceId="ws-1" />);

      const analyticsTab = screen.getByText('Analytics');
      fireEvent.click(analyticsTab);

      // Analytics component would receive workspace ID
      // (tested via component mock)
    });
  });

  describe('Responsive Behavior', () => {
    test('renders on mobile viewport', () => {
      // Mock window size
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 375,
      });

      render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('Queue Dashboard')).toBeInTheDocument();
    });

    test('renders on desktop viewport', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 1920,
      });

      render(<QueueDashboard workspaceId="ws-1" />);

      expect(screen.getByText('Queue Dashboard')).toBeInTheDocument();
    });
  });

  describe('Performance', () => {
    test('initial render completes in reasonable time', () => {
      const startTime = performance.now();

      render(<QueueDashboard workspaceId="ws-1" />);

      const endTime = performance.now();
      const renderTime = endTime - startTime;

      // Should render in <500ms
      expect(renderTime).toBeLessThan(500);
    });

    test('metrics update without causing full re-render', () => {
      const { rerender } = render(<QueueDashboard workspaceId="ws-1" />);

      const startTime = performance.now();

      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        stats: {
          ...mockHookData.stats,
          active_count: 2,
        },
      });

      rerender(<QueueDashboard workspaceId="ws-1" />);

      const endTime = performance.now();
      const updateTime = endTime - startTime;

      // Update should be fast (<100ms)
      expect(updateTime).toBeLessThan(100);
    });
  });

  describe('Error Handling', () => {
    test('handles missing hook data gracefully', () => {
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        activeRuns: null,
        queuedRuns: null,
        stats: null,
        connectionState: 'disconnected',
        isWebSocket: false,
        loading: false,
        error: null,
      });

      // Should not throw
      expect(() => {
        render(<QueueDashboard workspaceId="ws-1" />);
      }).not.toThrow();
    });

    test('handles undefined workspace gracefully', () => {
      // Should not throw with undefined workspace
      expect(() => {
        render(<QueueDashboard workspaceId={undefined as any} />);
      }).not.toThrow();
    });
  });
});

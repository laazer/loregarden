/**
 * Tests for ParallelQueueVisualization component
 * Covers slot display, queue items, drag-to-reorder, and real-time updates
 */

import { render, screen, fireEvent, within } from '@testing-library/react';
import { ParallelQueueVisualization } from '../ParallelQueueVisualization';
import * as useHook from '../../hooks/useParallelExecutionWS';

jest.mock('../../hooks/useParallelExecutionWS', () => ({
  useParallelExecutionWS: jest.fn(),
}));

describe('ParallelQueueVisualization', () => {
  const mockHookData = {
    activeRuns: [
      {
        run_id: 'run-1',
        ticket_id: 'feature-123',
        slot_number: 1,
        elapsed_seconds: 120,
        status: 'running',
      },
      {
        run_id: 'run-2',
        ticket_id: 'feature-124',
        slot_number: 2,
        elapsed_seconds: 60,
        status: 'running',
      },
    ],
    queuedRuns: [
      {
        run_id: 'run-3',
        ticket_id: 'feature-125',
        position: 1,
        wait_seconds: 150,
        estimated_start_at: new Date(Date.now() + 150000).toISOString(),
      },
      {
        run_id: 'run-4',
        ticket_id: 'feature-126',
        position: 2,
        wait_seconds: 450,
        estimated_start_at: new Date(Date.now() + 450000).toISOString(),
      },
    ],
    stats: {
      max_concurrent: 3,
      active_count: 2,
      available_slots: 1,
      queued_count: 2,
      queue_wait_time_minutes: 2,
    },
    connectionState: 'connected',
    isWebSocket: true,
    loading: false,
    error: null,
  };

  beforeEach(() => {
    (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue(mockHookData);
  });

  describe('Rendering', () => {
    test('renders component with all sections', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('Parallel Execution Queue')).toBeInTheDocument();
      expect(screen.getByText('Execution Slots')).toBeInTheDocument();
      expect(container.querySelector('.queue-list-section')).toBeInTheDocument();
    });

    test('displays connection status indicator', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('Connected')).toBeInTheDocument();
      expect(screen.getByText('connected')).toBeInTheDocument();
    });

    test('shows polling indicator when not WebSocket', () => {
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        isWebSocket: false,
      });

      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('Polling')).toBeInTheDocument();
    });
  });

  describe('System Status Overview', () => {
    test('displays slot usage correctly', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      // Should show 2/3 active slots
      expect(screen.getByText('Slot Usage')).toBeInTheDocument();
      expect(screen.getByText('2/3')).toBeInTheDocument();
    });

    test('displays queue length', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('Queue Length')).toBeInTheDocument();
      expect(screen.getAllByText('2')).toContainEqual(
        screen.getByText('2', { selector: '.overview-value' })
      );
    });

    test('displays estimated clear time', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const label = screen.getByText('Estimated Clear');
      const card = label.closest('.overview-card');
      expect(card).toHaveTextContent(/\d+m/); // Shows minutes
    });

    test('displays queue wait time', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('Wait Time')).toBeInTheDocument();
      expect(screen.getByText('2m')).toBeInTheDocument();
    });
  });

  describe('Execution Slots', () => {
    test('renders all slots', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('Slot 1')).toBeInTheDocument();
      expect(screen.getByText('Slot 2')).toBeInTheDocument();
      expect(screen.getByText('Slot 3')).toBeInTheDocument();
    });

    test('shows active run details in slot', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('feature-123')).toBeInTheDocument();
      expect(screen.getByText('feature-124')).toBeInTheDocument();
    });

    test('displays elapsed and estimated time', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      // Should show times like "2m / 5m"
      const timeDisplays = screen.getAllByText(/\d+[ms]/);
      expect(timeDisplays.length).toBeGreaterThan(0);
    });

    test('shows progress bar for active runs', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const progressBars = container.querySelectorAll('.progress-bar');
      expect(progressBars.length).toBeGreaterThan(0);
    });

    test('marks available slots', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(container.querySelector('.empty-text')).toHaveTextContent('Available');
    });
  });

  describe('Queue List', () => {
    test('renders queued runs', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('feature-125')).toBeInTheDocument();
      expect(screen.getByText('feature-126')).toBeInTheDocument();
    });

    test('shows queue positions', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('#1')).toBeInTheDocument();
      expect(screen.getByText('#2')).toBeInTheDocument();
    });

    test('displays wait time for each queued run', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      // Should show wait times, one per queued run
      const waitLabels = screen.getAllByText('Wait:');
      expect(waitLabels.length).toBe(2);
    });

    test('shows estimated start time', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const estLabels = screen.getAllByText('Est. start:');
      expect(estLabels.length).toBe(2);
    });

    test('displays queued badge', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const badges = screen.getAllByText('Queued', { selector: '.badge-text' });
      expect(badges.length).toBe(2);
    });
  });

  describe('Drag-to-Reorder', () => {
    test('queue items are draggable', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const queueItems = container.querySelectorAll('.queue-item');
      queueItems.forEach((item) => {
        expect(item).toHaveAttribute('draggable', 'true');
      });
    });

    test('shows drag handle', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const handles = container.querySelectorAll('.queue-item-handle');
      expect(handles.length).toBeGreaterThan(0);
    });

    test('applies dragging class during drag', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const queueItem = container.querySelector('.queue-item') as HTMLElement;
      fireEvent.dragStart(queueItem);

      // After drag starts, item should be marked as dragging
      expect(queueItem.classList.contains('dragging')).toBe(true);
    });

    test('shows drag-over styling on drop zone', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const queueItems = container.querySelectorAll('.queue-item');
      const dragSource = queueItems[0] as HTMLElement;
      const dropTarget = queueItems[1] as HTMLElement;

      fireEvent.dragStart(dragSource);
      fireEvent.dragOver(dropTarget);

      expect(dropTarget.classList.contains('drag-over')).toBe(true);
    });

    test('clears drag state on drag end', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const queueItem = container.querySelector('.queue-item') as HTMLElement;
      fireEvent.dragStart(queueItem);
      fireEvent.dragEnd(queueItem);

      expect(queueItem.classList.contains('dragging')).toBe(false);
    });

    test('shows reorder hint', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText(/Drag items to reorder/)).toBeInTheDocument();
    });
  });

  describe('Empty States', () => {
    test('shows empty state when no active or queued runs', () => {
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        activeRuns: [],
        queuedRuns: [],
      });

      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('All slots available')).toBeInTheDocument();
      expect(screen.getByText('Ready for parallel execution')).toBeInTheDocument();
    });

    test('does not show queue section when no queued runs', () => {
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        queuedRuns: [],
      });

      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const queueListSection = container.querySelector('.queue-list-section');
      expect(queueListSection).not.toBeInTheDocument();
    });
  });

  describe('Real-time Updates', () => {
    test('updates when hook data changes', () => {
      const { rerender } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('2/3')).toBeInTheDocument();

      // Update hook data
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        stats: {
          ...mockHookData.stats,
          active_count: 3,
        },
      });

      rerender(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByText('3/3')).toBeInTheDocument();
    });

    test('updates progress bars in real-time', () => {
      const { container, rerender } = render(
        <ParallelQueueVisualization workspaceId="ws-1" />
      );

      const initialProgressFill = container.querySelector('.progress-fill');
      const initialWidth = initialProgressFill?.getAttribute('style');

      // Update elapsed time
      (useHook.useParallelExecutionWS as jest.Mock).mockReturnValue({
        ...mockHookData,
        activeRuns: [
          {
            ...mockHookData.activeRuns[0],
            elapsed_seconds: 250, // Increased from 120
          },
          mockHookData.activeRuns[1],
        ],
      });

      rerender(<ParallelQueueVisualization workspaceId="ws-1" />);

      const updatedProgressFill = container.querySelector('.progress-fill');
      const updatedWidth = updatedProgressFill?.getAttribute('style');

      // Width should have changed
      expect(updatedWidth).not.toBe(initialWidth);
    });
  });

  describe('Legend', () => {
    test('displays legend with all states', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      const legend = within(container.querySelector('.queue-legend')!);
      expect(legend.getByText('Running')).toBeInTheDocument();
      expect(legend.getByText('Queued')).toBeInTheDocument();
      expect(legend.getByText('Available')).toBeInTheDocument();
    });
  });

  describe('Props', () => {
    test('passes workspaceId to hook', () => {
      render(<ParallelQueueVisualization workspaceId="ws-123" />);

      expect(useHook.useParallelExecutionWS).toHaveBeenCalledWith('ws-123');
    });
  });

  describe('Accessibility', () => {
    test('renders semantic HTML', () => {
      const { container } = render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(container.querySelector('h2')).toBeInTheDocument();
      expect(container.querySelector('h3')).toBeInTheDocument();
    });

    test('queue items have test IDs for accessibility', () => {
      render(<ParallelQueueVisualization workspaceId="ws-1" />);

      expect(screen.getByTestId('slot-1')).toBeInTheDocument();
      expect(screen.getByTestId('queue-item-1')).toBeInTheDocument();
    });
  });
});

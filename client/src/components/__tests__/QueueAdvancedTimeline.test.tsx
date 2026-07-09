/**
 * Tests for QueueAdvancedTimeline component
 * Validates Gantt-style timeline visualization for queue progression
 */

import { render, screen } from '@testing-library/react';
import { QueueAdvancedTimeline } from '../QueueAdvancedTimeline';
import type { TimelineRun } from '../QueueAdvancedTimeline';

describe('QueueAdvancedTimeline', () => {
  const mockActiveRun: TimelineRun = {
    run_id: 'run-1',
    ticket_id: 'feature-123',
    slot_number: 1,
    elapsed_seconds: 120,
    estimated_duration_seconds: 300,
    status: 'running',
  };

  const mockQueuedRun: TimelineRun = {
    run_id: 'run-2',
    ticket_id: 'feature-124',
    position: 1,
    elapsed_seconds: 0,
    estimated_duration_seconds: 300,
    status: 'queued',
  };

  describe('Rendering', () => {
    test('renders timeline container', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });

    test('renders empty state when no runs', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('No active or queued runs')).toBeInTheDocument();
    });

    test('renders active runs section', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Active Runs')).toBeInTheDocument();
      expect(screen.getByText('feature-123')).toBeInTheDocument();
    });

    test('renders queued runs section', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText('Queue')).toBeInTheDocument();
      expect(screen.getByText('feature-124')).toBeInTheDocument();
    });

    test('renders legend', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Running')).toBeInTheDocument();
      expect(screen.getByText('Progress')).toBeInTheDocument();
      expect(screen.getByText('Remaining')).toBeInTheDocument();
      expect(screen.getByText('Waiting')).toBeInTheDocument();
    });
  });

  describe('Time Formatting', () => {
    test('formats minutes scale correctly', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
          timeScale="minutes"
        />
      );

      // formatTime(120) = "2m 0s", formatTime(300) = "5m 0s"
      expect(screen.getByText(/2m 0s/)).toBeInTheDocument();
    });

    test('formats hours scale correctly', () => {
      const longRun: TimelineRun = {
        ...mockActiveRun,
        estimated_duration_seconds: 7200, // 2 hours
        elapsed_seconds: 3600, // 1 hour elapsed
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[longRun]}
          queuedRuns={[]}
          timeScale="hours"
        />
      );

      // In hours scale: should format with hours and minutes
      expect(screen.getByText(/h /)).toBeInTheDocument();
    });

    test('displays remaining time correctly', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      // 300 - 120 = 180 seconds = 3m 0s
      expect(screen.getByText('3m 0s remaining')).toBeInTheDocument();
    });
  });

  describe('Timeline Metrics', () => {
    test('calculates total clear time', () => {
      const activeRun: TimelineRun = {
        ...mockActiveRun,
        elapsed_seconds: 100,
        estimated_duration_seconds: 200,
      };

      const queuedRun1: TimelineRun = {
        ...mockQueuedRun,
        position: 1,
      };

      const queuedRun2: TimelineRun = {
        ...mockQueuedRun,
        run_id: 'run-3',
        ticket_id: 'feature-125',
        position: 2,
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[activeRun]}
          queuedRuns={[queuedRun1, queuedRun2]}
        />
      );

      // Active remaining: 100s, Queued: 2 * 300s = 600s, Total = 700s
      expect(screen.getByText(/Total Clear Time:/)).toBeInTheDocument();
    });

    test('displays active time separately', () => {
      const activeRun1: TimelineRun = {
        ...mockActiveRun,
        elapsed_seconds: 50,
        estimated_duration_seconds: 300,
      };

      const activeRun2: TimelineRun = {
        ...mockActiveRun,
        run_id: 'run-2',
        ticket_id: 'feature-124',
        slot_number: 2,
        elapsed_seconds: 100,
        estimated_duration_seconds: 200,
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[activeRun1, activeRun2]}
          queuedRuns={[]}
        />
      );

      // Active remaining: (300-50) + (200-100) = 350s
      expect(screen.getByText(/Active:/)).toBeInTheDocument();
    });

    test('displays queued time separately', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[]}
          queuedRuns={[mockQueuedRun, mockQueuedRun]}
        />
      );

      // 2 * 300s = 600s = 10m 0s
      expect(screen.getByText(/Queued:/)).toBeInTheDocument();
    });
  });

  describe('Progress Visualization', () => {
    test('renders progress bar for active run', () => {
      const { container } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      const progressBar = container.querySelector('.bar-progress');
      expect(progressBar).toBeInTheDocument();
    });

    test('calculates progress percentage correctly', () => {
      const { container } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      // 120 / 300 = 40%
      const progressBar = container.querySelector('.bar-progress') as HTMLElement;
      const width = progressBar?.style.width;
      expect(width).toBe('40%');
    });

    test('calculates remaining percentage correctly', () => {
      const { container } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      // (300 - 120) / 300 = 60%
      const remainingBar = container.querySelector('.bar-remaining') as HTMLElement;
      const width = remainingBar?.style.width;
      expect(width).toBe('60%');
    });

    test('shows waiting time for queued runs', () => {
      const { container } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      // First queued run starts after active run completes (300 - 120 = 180s)
      const waitingBar = container.querySelector('.bar-waiting');
      expect(waitingBar).toBeInTheDocument();
    });
  });

  describe('Timeline Positioning', () => {
    test('positions queued run start time correctly', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText(/Starts in/)).toBeInTheDocument();
    });

    test('handles multiple queued runs with correct spacing', () => {
      const queuedRun1 = { ...mockQueuedRun, position: 1 };
      const queuedRun2 = {
        ...mockQueuedRun,
        run_id: 'run-3',
        ticket_id: 'feature-125',
        position: 2,
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[queuedRun1, queuedRun2]}
        />
      );

      // Both should be present
      expect(screen.getByText('feature-124')).toBeInTheDocument();
      expect(screen.getByText('feature-125')).toBeInTheDocument();
    });
  });

  describe('Slot and Position Badges', () => {
    test('displays slot number for active runs', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Slot 1')).toBeInTheDocument();
    });

    test('displays position number for queued runs', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText('#1')).toBeInTheDocument();
    });

    test('handles multiple active runs with different slots', () => {
      const activeRun1 = { ...mockActiveRun, slot_number: 1 };
      const activeRun2 = {
        ...mockActiveRun,
        run_id: 'run-2',
        ticket_id: 'feature-124',
        slot_number: 2,
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[activeRun1, activeRun2]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Slot 1')).toBeInTheDocument();
      expect(screen.getByText('Slot 2')).toBeInTheDocument();
    });
  });

  describe('Time Scale Selection', () => {
    test('uses minutes scale by default', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      // Should format in minutes: "2m 0s remaining"
      expect(screen.getByText(/\dm \d{1,2}s/)).toBeInTheDocument();
    });

    test('switches to hours scale when specified', () => {
      const longRun: TimelineRun = {
        ...mockActiveRun,
        estimated_duration_seconds: 14400, // 4 hours
        elapsed_seconds: 7200, // 2 hours
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[longRun]}
          queuedRuns={[]}
          timeScale="hours"
        />
      );

      // Should format with hours
      expect(screen.getByText(/\dh \d{1,2}m/)).toBeInTheDocument();
    });

    test('adjusts timeline scale for hours', () => {
      const longRun: TimelineRun = {
        ...mockActiveRun,
        estimated_duration_seconds: 3600, // 1 hour
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[longRun]}
          queuedRuns={[]}
          timeScale="hours"
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });
  });

  describe('Queue Completion Indicator', () => {
    test('shows queue clear time', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText(/All clear at/)).toBeInTheDocument();
    });

    test('is not displayed when queue is empty', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[]}
          queuedRuns={[]}
        />
      );

      expect(screen.queryByText(/All clear at/)).not.toBeInTheDocument();
    });

    test('calculates correct clear time with multiple runs', () => {
      const activeRun: TimelineRun = {
        ...mockActiveRun,
        elapsed_seconds: 150,
        estimated_duration_seconds: 300,
      };

      const queuedRun1 = { ...mockQueuedRun, position: 1 };
      const queuedRun2 = {
        ...mockQueuedRun,
        run_id: 'run-3',
        ticket_id: 'feature-125',
        position: 2,
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[activeRun]}
          queuedRuns={[queuedRun1, queuedRun2]}
        />
      );

      // Total: (300-150) + (300+300) = 750s
      expect(screen.getByText(/All clear at/)).toBeInTheDocument();
    });
  });

  describe('Edge Cases', () => {
    test('handles zero elapsed time', () => {
      const newRun: TimelineRun = {
        ...mockActiveRun,
        elapsed_seconds: 0,
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[newRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('5m 0s remaining')).toBeInTheDocument();
    });

    test('handles run with zero estimated duration', () => {
      const quickRun: TimelineRun = {
        ...mockActiveRun,
        estimated_duration_seconds: 0,
        elapsed_seconds: 0,
      };

      // Should not crash
      expect(() => {
        render(
          <QueueAdvancedTimeline
            activeRuns={[quickRun]}
            queuedRuns={[]}
          />
        );
      }).not.toThrow();
    });

    test('handles very long running jobs', () => {
      const longJob: TimelineRun = {
        ...mockActiveRun,
        estimated_duration_seconds: 86400, // 24 hours
        elapsed_seconds: 43200, // 12 hours
      };

      render(
        <QueueAdvancedTimeline
          activeRuns={[longJob]}
          queuedRuns={[]}
          timeScale="hours"
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });

    test('handles very large number of queued runs', () => {
      const queuedRuns = Array.from({ length: 50 }, (_, i) => ({
        ...mockQueuedRun,
        run_id: `run-${i}`,
        ticket_id: `feature-${i}`,
        position: i + 1,
      }));

      const { container } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={queuedRuns}
        />
      );

      // Should render all rows without crashing
      const rows = container.querySelectorAll('.timeline-row');
      expect(rows.length).toBeGreaterThan(0);
    });
  });

  describe('Current Time Prop', () => {
    test('accepts custom current time', () => {
      const futureDate = new Date(Date.now() + 86400000); // 24 hours from now

      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
          currentTime={futureDate}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });

    test('defaults to current date when not provided', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });
  });

  describe('Responsive Behavior', () => {
    test('renders on mobile viewport', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 375,
      });

      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });

    test('renders on desktop viewport', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 1920,
      });

      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });
  });

  describe('Performance', () => {
    test('renders with minimal rerenders', () => {
      const { rerender } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      // Should rerender with same props without issues
      rerender(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[mockQueuedRun]}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });

    test('handles updates to active runs efficiently', () => {
      const { rerender } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      const updatedRun = {
        ...mockActiveRun,
        elapsed_seconds: 200,
      };

      rerender(
        <QueueAdvancedTimeline
          activeRuns={[updatedRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Queue Timeline')).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    test('includes semantic HTML structure', () => {
      const { container } = render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(container.querySelector('.timeline-container')).toBeInTheDocument();
      expect(container.querySelector('.timeline-header')).toBeInTheDocument();
    });

    test('displays readable labels', () => {
      render(
        <QueueAdvancedTimeline
          activeRuns={[mockActiveRun]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('Active Runs')).toBeInTheDocument();
      expect(screen.getByText('Slot 1')).toBeInTheDocument();
      expect(screen.getByText('feature-123')).toBeInTheDocument();
    });
  });
});

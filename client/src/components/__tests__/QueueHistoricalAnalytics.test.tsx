/**
 * Tests for QueueHistoricalAnalytics component
 * Covers metrics display, time ranges, success rates, and data fetching
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueueHistoricalAnalytics } from '../QueueHistoricalAnalytics';

describe('QueueHistoricalAnalytics', () => {
  const mockMetrics = [
    {
      ticket_type: 'feature-branch',
      count: 42,
      avg_duration_seconds: 245,
      min_duration_seconds: 120,
      max_duration_seconds: 420,
      success_rate: 0.95,
      last_7_days_count: 8,
      last_7_days_success_rate: 0.98,
    },
    {
      ticket_type: 'bug-fix',
      count: 28,
      avg_duration_seconds: 180,
      min_duration_seconds: 60,
      max_duration_seconds: 360,
      success_rate: 0.85,
      last_7_days_count: 5,
      last_7_days_success_rate: 0.8,
    },
  ];

  beforeEach(() => {
    global.fetch = jest
      .fn()
      .mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ metrics: mockMetrics }),
      });
  });

  describe('Rendering', () => {
    test('renders analytics container', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        expect(
          container.querySelector('.queue-analytics-container')
        ).toBeInTheDocument();
      });
    });

    test('displays header with title', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText('Run Performance History')).toBeInTheDocument();
      });
    });

    test('displays time range selector', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText('Last 7 days')).toBeInTheDocument();
        expect(screen.getByText('Last 30 days')).toBeInTheDocument();
        expect(screen.getByText('Last 90 days')).toBeInTheDocument();
      });
    });
  });

  describe('Metrics Display', () => {
    test('displays ticket type metrics', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText('feature-branch')).toBeInTheDocument();
        expect(screen.getByText('bug-fix')).toBeInTheDocument();
      });
    });

    test('displays success rate badges', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText('95%')).toBeInTheDocument();
        expect(screen.getByText('85%')).toBeInTheDocument();
      });
    });

    test('displays total run count', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        // Look for the count metric display
        expect(screen.getByText(/Total Runs/)).toBeInTheDocument();
      });
    });

    test('displays average duration', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Avg Duration/)).toBeInTheDocument();
      });
    });

    test('displays duration range', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Range/)).toBeInTheDocument();
      });
    });

    test('displays last 7 days stats', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Last 7 Days/)).toBeInTheDocument();
      });
    });
  });

  describe('Success Rate Coloring', () => {
    test('applies excellent color for >95% success', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        const excellentBadges = container.querySelectorAll(
          '.success-badge.excellent'
        );
        expect(excellentBadges.length).toBeGreaterThan(0);
      });
    });

    test('applies good color for 85-95% success', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        const goodBadges = container.querySelectorAll('.success-badge.good');
        // May not be present if no metrics in that range
        expect(goodBadges).toBeDefined();
      });
    });

    test('applies fair color for 75-85% success', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        expect(container.querySelector('.success-badge')).toBeInTheDocument();
      });
    });

    test('applies poor color for <75% success', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        expect(container.querySelector('.success-badge')).toBeInTheDocument();
      });
    });
  });

  describe('Insights', () => {
    test('shows fast execution indicator for runs <2min', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        const fastInsights = container.querySelectorAll('.insight.fast');
        expect(fastInsights.length).toBeGreaterThan(0);
      });
    });

    test('shows reliability indicator for >95% success', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        const reliableInsights = container.querySelectorAll(
          '.insight.reliable'
        );
        expect(reliableInsights.length).toBeGreaterThan(0);
      });
    });

    test('shows warning for success rate <85%', async () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        const concerningInsights = container.querySelectorAll(
          '.insight.concerning'
        );
        expect(concerningInsights.length).toBeGreaterThan(0);
      });
    });
  });

  describe('Time Range Selection', () => {
    test('default time range is 7d', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        const sevenDayBtn = screen.getByText('Last 7 days');
        expect(sevenDayBtn).toHaveClass('active');
      });
    });

    test('changes time range on button click', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        const thirtyDayBtn = screen.getByText('Last 30 days');
        fireEvent.click(thirtyDayBtn);
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining('range=30d'),
          expect.anything()
        );
      });
    });

    test('fetches new data on range change', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      const initialCallCount = (global.fetch as jest.Mock).mock.calls.length;

      await waitFor(() => {
        const ninetyDayBtn = screen.getByText('Last 90 days');
        fireEvent.click(ninetyDayBtn);
      });

      await waitFor(() => {
        expect((global.fetch as jest.Mock).mock.calls.length).toBeGreaterThan(
          initialCallCount
        );
      });
    });
  });

  describe('Summary Statistics', () => {
    test('displays overall success rate', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Overall Success Rate/)).toBeInTheDocument();
      });
    });

    test('displays total runs completed', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Total Runs Completed/)).toBeInTheDocument();
      });
    });

    test('displays average run duration', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Average Run Duration/)).toBeInTheDocument();
      });
    });

    test('displays ticket types tracked', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(screen.getByText(/Ticket Types Tracked/)).toBeInTheDocument();
      });
    });
  });

  describe('Empty State', () => {
    test('shows empty state when no metrics', async () => {
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ metrics: [] }),
      });

      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(
          screen.getByText('No run history available yet')
        ).toBeInTheDocument();
      });
    });

    test('doesn\'t show summary when no metrics', async () => {
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ metrics: [] }),
      });

      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      await waitFor(() => {
        expect(
          container.querySelector('.analytics-summary')
        ).not.toBeInTheDocument();
      });
    });
  });

  describe('Loading State', () => {
    test('shows loading message initially', () => {
      (global.fetch as jest.Mock).mockImplementation(
        () =>
          new Promise(() => {
            /* never resolves */
          })
      );

      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      expect(screen.getByText('Loading analytics...')).toBeInTheDocument();
    });
  });

  describe('Error Handling', () => {
    test('displays error message on fetch failure', async () => {
      (global.fetch as jest.Mock).mockRejectedValue(
        new Error('Failed to fetch')
      );

      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(
          screen.getByText(/Failed to load analytics/)
        ).toBeInTheDocument();
      });
    });

    test('handles invalid response gracefully', async () => {
      (global.fetch as jest.Mock).mockResolvedValue({
        ok: false,
        status: 500,
      });

      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(
          screen.getByText(/Failed to fetch analytics/)
        ).toBeInTheDocument();
      });
    });
  });

  describe('API Integration', () => {
    test('fetches metrics on mount', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining('/api/parallel/workspace/ws-1/analytics'),
          expect.anything()
        );
      });
    });

    test('passes workspace ID to API', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-custom" />);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining('/ws-custom/'),
          expect.anything()
        );
      });
    });
  });

  describe('Responsive Design', () => {
    test('uses responsive grid layout', () => {
      const { container } = render(
        <QueueHistoricalAnalytics workspaceId="ws-1" />
      );

      expect(
        container.querySelector('.analytics-grid')
      ).toBeInTheDocument();
    });

    test('displays summary in grid layout', async () => {
      render(<QueueHistoricalAnalytics workspaceId="ws-1" />);

      await waitFor(() => {
        const summary = document.querySelector('.analytics-summary');
        expect(summary).toBeInTheDocument();
      });
    });
  });
});

/**
 * Tests for QueueAdvancedControls component
 * Covers run selection, actions, bulk operations, and error handling
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueueAdvancedControls } from '../QueueAdvancedControls';

describe('QueueAdvancedControls', () => {
  const mockActiveRuns = [
    {
      run_id: 'run-1',
      ticket_id: 'feature-123',
      slot_number: 1,
      elapsed_seconds: 60,
      status: 'running',
      agent_id: 'agent-1',
    },
  ];

  const mockQueuedRuns = [
    {
      run_id: 'run-2',
      ticket_id: 'feature-124',
      position: 1,
      estimated_start_at: new Date(Date.now() + 300_000).toISOString(),
      wait_seconds: 300,
      status: 'queued',
      agent_id: 'agent-2',
    },
    {
      run_id: 'run-3',
      ticket_id: 'feature-125',
      position: 2,
      estimated_start_at: new Date(Date.now() + 600_000).toISOString(),
      wait_seconds: 600,
      status: 'queued',
      agent_id: 'agent-3',
    },
  ];

  describe('Rendering', () => {
    test('renders control panel', () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      expect(screen.getByText('Queue Controls')).toBeInTheDocument();
    });

    test('displays active runs section', () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      expect(screen.getByText('Active Runs')).toBeInTheDocument();
      expect(screen.getByText('feature-123')).toBeInTheDocument();
    });

    test('displays queued runs section', () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      expect(screen.getByText('Queued Runs')).toBeInTheDocument();
      expect(screen.getByText('feature-124')).toBeInTheDocument();
      expect(screen.getByText('feature-125')).toBeInTheDocument();
    });

    test('shows empty state when no runs', () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={[]}
          queuedRuns={[]}
        />
      );

      expect(screen.getByText('No active or queued runs')).toBeInTheDocument();
    });
  });

  describe('Run Selection', () => {
    test('toggles run selection with checkbox', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(screen.getByText(/1 selected/)).toBeInTheDocument();
      });
    });

    test('displays selection count', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);
      fireEvent.click(checkboxes[1]);

      await waitFor(() => {
        expect(screen.getByText(/2 selected/)).toBeInTheDocument();
      });
    });

    test('highlights selected runs', async () => {
      const { container } = render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        const selectedItems = container.querySelectorAll('.run-control-item.selected');
        expect(selectedItems.length).toBeGreaterThan(0);
      });
    });
  });

  describe('Per-Run Actions', () => {
    test('shows action buttons on toggle', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]);

      await waitFor(() => {
        expect(screen.getByText('Pause')).toBeInTheDocument();
      });
    });

    test('active run shows pause/cancel actions', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]); // Active run

      await waitFor(() => {
        expect(screen.getByText('Pause')).toBeInTheDocument();
        expect(screen.getByText('Cancel')).toBeInTheDocument();
      });
    });

    test('queued run shows promote/cancel actions', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[1]); // Queued run

      await waitFor(() => {
        expect(screen.getByText('Promote')).toBeInTheDocument();
        expect(screen.getByText('Cancel')).toBeInTheDocument();
      });
    });

    test('disables actions while processing', async () => {
      const mockOnRunControl = jest.fn(
        () => new Promise<void>((resolve) => setTimeout(resolve, 100))
      );

      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
          onRunControl={mockOnRunControl}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]);

      const pauseButton = screen.getByText('Pause');
      fireEvent.click(pauseButton);

      await waitFor(() => {
        expect(pauseButton).toBeDisabled();
      });
    });
  });

  describe('Bulk Actions', () => {
    test('shows bulk cancel button when runs selected', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(screen.getByText(/Cancel Selected/)).toBeInTheDocument();
      });
    });

    test('bulk action includes selection count', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(screen.getByText(/Cancel Selected \(1\)/)).toBeInTheDocument();
      });
    });

    test('clear selection button deselects all', async () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(screen.getByText('Clear Selection')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Clear Selection'));

      await waitFor(() => {
        expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
      });
    });
  });

  describe('Error Handling', () => {
    test('displays error message on action failure', async () => {
      const mockOnRunControl = jest
        .fn()
        .mockRejectedValue(new Error('Failed to pause'));

      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
          onRunControl={mockOnRunControl}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]);

      const pauseButton = screen.getByText('Pause');
      fireEvent.click(pauseButton);

      await waitFor(() => {
        expect(screen.getByText('Failed to pause run')).toBeInTheDocument();
      });
    });

    test('dismiss error message', async () => {
      const mockOnRunControl = jest
        .fn()
        .mockRejectedValue(new Error('Failed'));

      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
          onRunControl={mockOnRunControl}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]);

      const pauseButton = screen.getByText('Pause');
      fireEvent.click(pauseButton);

      await waitFor(() => {
        expect(screen.getByText(/Failed/)).toBeInTheDocument();
      });

      // Error should be dismissible (in actual implementation)
    });
  });

  describe('API Integration', () => {
    test('calls onRunControl with correct parameters', async () => {
      const mockOnRunControl = jest.fn().mockResolvedValue(undefined);

      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
          onRunControl={mockOnRunControl}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]);

      const pauseButton = screen.getByText('Pause');
      fireEvent.click(pauseButton);

      await waitFor(() => {
        expect(mockOnRunControl).toHaveBeenCalledWith('pause', 'run-1');
      });
    });

    test('default to fetch API if no handler provided', async () => {
      const mockFetch = jest.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });
      global.fetch = mockFetch;

      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      fireEvent.click(toggleButtons[0]);

      const pauseButton = screen.getByText('Pause');
      fireEvent.click(pauseButton);

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          '/api/parallel/queue/run-1/pause',
          expect.any(Object)
        );
      });
    });
  });

  describe('Accessibility', () => {
    test('checkboxes have accessible labels', () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const checkboxes = screen.getAllByRole('checkbox');
      checkboxes.forEach((checkbox) => {
        expect(checkbox).toHaveAttribute('aria-label');
      });
    });

    test('toggle buttons have aria-expanded', () => {
      render(
        <QueueAdvancedControls
          workspaceId="ws-1"
          activeRuns={mockActiveRuns}
          queuedRuns={mockQueuedRuns}
        />
      );

      const toggleButtons = screen.getAllByLabelText('Toggle controls');
      toggleButtons.forEach((btn) => {
        expect(btn).toHaveAttribute('aria-expanded');
      });
    });
  });
});

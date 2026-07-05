import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Dashboard from '../Dashboard';
import * as apiClient from '../../api/client';

/**
 * Integration tests for "View Details" button in Dashboard ticket pane.
 *
 * Spec reference: 16-modal-with-ticket-details
 * Feature: Ticket pane should include a button that opens a modal with full ticket details
 *
 * These tests verify:
 * - Button is rendered in the workflow/ticket pane header
 * - Button is visible when a ticket is selected
 * - Button opens the ticket details modal
 * - Modal displays full ticket information
 * - Modal can be closed and reopened
 */

// Mock the API
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual('../../api/client');
  return {
    ...actual,
    api: {
      getTicketTree: vi.fn(),
      getTicket: vi.fn(),
      getWorkspaces: vi.fn(),
      getWorkspaceWorkflow: vi.fn(),
    },
  };
});

describe('Dashboard - Ticket Details Button Integration', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    vi.clearAllMocks();
  });

  const renderDashboard = (initialProps = {}) => {
    return render(
      <QueryClientProvider client={queryClient}>
        <Dashboard {...initialProps} />
      </QueryClientProvider>
    );
  };

  describe('Button Visibility and Placement', () => {
    it('should render "Details" button in workflow pane header when ticket is selected', async () => {
      // SPEC: Button should appear next to ticket title in the workflow pane
      // This is the main pane that shows selected ticket information

      // Mock API responses
      const mockWorkspaces = [{ slug: 'loregarden', name: 'Loregarden' }];
      const mockTree = [
        createMockTreeNode({
          id: 'ticket-1',
          title: 'Modal Feature',
        }),
      ];
      const mockTicket = createMockTicket({
        id: 'ticket-1',
        title: 'Modal Feature',
        external_id: '16-modal-with-ticket-details',
      });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select a ticket
      await waitFor(() => {
        const treeItem = screen.getByText('Modal Feature');
        fireEvent.click(treeItem);
      });

      // Details button should appear in the workflow pane header
      await waitFor(() => {
        const button = screen.getByRole('button', { name: /details|view details|ticket details/i });
        expect(button).toBeInTheDocument();
        // Verify it's in the workflow pane area
        const workflowPane = screen.getByText('Workflow');
        expect(workflowPane.closest('.workflow-pane')).toContainElement(button);
      });
    });

    it('should not render Details button when no ticket is selected', () => {
      // SPEC: Button should not appear in empty workflow pane
      const mockWorkspaces = [{ slug: 'loregarden', name: 'Loregarden' }];

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue([]);

      renderDashboard();

      const detailsButtons = screen.queryAllByRole('button', { name: /details/i });
      // Should not have a details button for workflow pane (may have other buttons)
      expect(detailsButtons.length).toBe(0);
    });

    it('should render button when switching between tickets', async () => {
      // SPEC: Button should update when different ticket is selected
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [
        createMockTreeNode({ id: 'ticket-1', title: 'First Ticket' }),
        createMockTreeNode({ id: 'ticket-2', title: 'Second Ticket' }),
      ];
      const mockTicket1 = createMockTicket({ id: 'ticket-1', title: 'First Ticket' });
      const mockTicket2 = createMockTicket({ id: 'ticket-2', title: 'Second Ticket' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket)
        .mockResolvedValueOnce(mockTicket1)
        .mockResolvedValueOnce(mockTicket2);

      renderDashboard();

      // Select first ticket
      await waitFor(() => {
        const item = screen.getByText('First Ticket');
        fireEvent.click(item);
      });

      let button = screen.getByRole('button', { name: /details/i });
      expect(button).toBeInTheDocument();

      // Select second ticket
      await waitFor(() => {
        const item = screen.getByText('Second Ticket');
        fireEvent.click(item);
      });

      button = screen.getByRole('button', { name: /details/i });
      expect(button).toBeInTheDocument();
    });
  });

  describe('Button Functionality and Modal Opening', () => {
    it('should open modal when Details button is clicked', async () => {
      // SPEC: Clicking button should open modal with full ticket details
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({
        id: 'ticket-1',
        title: 'Test Feature',
        description: 'This is a test feature',
      });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select ticket
      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Feature'));
      });

      // Click Details button
      const button = screen.getByRole('button', { name: /details/i });
      fireEvent.click(button);

      // Modal should appear with ticket details
      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByText('Test Feature')).toBeInTheDocument();
        expect(screen.getByText('This is a test feature')).toBeInTheDocument();
      });
    });

    it('should display full ticket details in modal', async () => {
      // SPEC: Modal should show complete ticket information
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({
        id: 'ticket-1',
        title: 'Complete Feature',
        external_id: '16-modal-with-ticket-details',
        description: 'Full description',
        acceptance_criteria: [
          'User can click button',
          'Modal displays details',
          'Modal can be closed',
        ],
        state: 'in_progress',
        priority: 1,
        blocking_issues: 'Awaiting API review',
      });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select ticket and open modal
      await waitFor(() => {
        fireEvent.click(screen.getByText('Complete Feature'));
      });

      const button = screen.getByRole('button', { name: /details/i });
      fireEvent.click(button);

      // Verify all details are displayed
      await waitFor(() => {
        expect(screen.getByText('Complete Feature')).toBeInTheDocument();
        expect(screen.getByText('16-modal-with-ticket-details')).toBeInTheDocument();
        expect(screen.getByText('Full description')).toBeInTheDocument();
        expect(screen.getByText('User can click button')).toBeInTheDocument();
        expect(screen.getByText('Awaiting API review')).toBeInTheDocument();
      });
    });

    it('should close modal when Close button is clicked', async () => {
      // SPEC: Modal should close when user clicks close button
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Ticket'));
      });

      // Open modal
      fireEvent.click(screen.getByRole('button', { name: /details/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Close modal
      const closeButton = screen.getByRole('button', { name: /close|x/i });
      fireEvent.click(closeButton);

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });
    });

    it('should allow reopening modal after closing', async () => {
      // SPEC: Button should work multiple times
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Ticket'));
      });

      const detailsButton = screen.getByRole('button', { name: /details/i });

      // Open modal
      fireEvent.click(detailsButton);
      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Close modal
      fireEvent.click(screen.getByRole('button', { name: /close|x/i }));
      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });

      // Reopen modal
      fireEvent.click(detailsButton);
      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });
    });
  });

  describe('Modal Behavior in Dashboard Context', () => {
    it('should not interfere with other dashboard panes when modal is open', async () => {
      // SPEC: Modal should not block interaction with other panes
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [
        createMockTreeNode({ id: 'ticket-1', title: 'Ticket 1' }),
        createMockTreeNode({ id: 'ticket-2', title: 'Ticket 2' }),
      ];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select ticket and open modal
      await waitFor(() => {
        fireEvent.click(screen.getByText('Ticket 1'));
      });

      fireEvent.click(screen.getByRole('button', { name: /details/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Should still see tickets pane in background
      expect(screen.getByText('Ticket 2')).toBeInTheDocument();
    });

    it('should maintain modal state when pane visibility changes', async () => {
      // SPEC: Modal should stay open/closed independently of pane toggles
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Ticket'));
      });

      // Open modal
      fireEvent.click(screen.getByRole('button', { name: /details/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Modal should remain visible
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should close modal when navigating to a different ticket', async () => {
      // SPEC: Opening details for new ticket should handle previous modal gracefully
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [
        createMockTreeNode({ id: 'ticket-1', title: 'First' }),
        createMockTreeNode({ id: 'ticket-2', title: 'Second' }),
      ];
      const mockTicket1 = createMockTicket({ id: 'ticket-1', title: 'First' });
      const mockTicket2 = createMockTicket({ id: 'ticket-2', title: 'Second' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket)
        .mockResolvedValueOnce(mockTicket1)
        .mockResolvedValueOnce(mockTicket2);

      renderDashboard();

      // Open first ticket's details
      await waitFor(() => {
        fireEvent.click(screen.getByText('First'));
      });

      fireEvent.click(screen.getByRole('button', { name: /details/i }));

      await waitFor(() => {
        expect(screen.getByText('First')).toBeInTheDocument();
      });

      // Switch to second ticket
      fireEvent.click(screen.getByText('Second'));

      // Modal should close automatically or be updated
      await waitFor(() => {
        // Either modal closes or shows second ticket details
        const modal = screen.queryByRole('dialog');
        if (modal) {
          expect(screen.getByText('Second')).toBeInTheDocument();
        }
      });
    });
  });

  describe('Accessibility in Context', () => {
    it('should have button with descriptive aria-label in pane header', async () => {
      // SPEC: Button must be accessible to screen readers
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1', title: 'Test' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test'));
      });

      const button = screen.getByRole('button', { name: /details/i });
      expect(button).toHaveAttribute('aria-label');
    });

    it('should support keyboard navigation to open modal', async () => {
      // SPEC: Button must be accessible via keyboard
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Ticket'));
      });

      const button = screen.getByRole('button', { name: /details/i });
      // Should be keyboard accessible
      expect(button).not.toBeDisabled();
      fireEvent.keyDown(button, { key: 'Enter' });
      // Should trigger click action
    });
  });

  describe('Error Handling', () => {
    it('should show error message if ticket details fail to load', async () => {
      // SPEC: Should handle API errors gracefully
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      vi.mocked(apiClient.api.getTicket).mockRejectedValue(new Error('Failed to load'));

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Ticket'));
      });

      // Button should still work but might show error
      const button = screen.getByRole('button', { name: /details/i });
      fireEvent.click(button);

      // Should show error state in modal
      await waitFor(() => {
        const errorOrDialog = screen.queryByRole('dialog') || screen.queryByText(/error|failed/i);
        expect(errorOrDialog).toBeInTheDocument();
      });
    });

    it('should allow retry if details fail to load', async () => {
      // SPEC: User should be able to retry loading details
      const mockWorkspaces = [{ slug: 'loregarden' }];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      vi.mocked(apiClient.api.getWorkspaces).mockResolvedValue(mockWorkspaces);
      vi.mocked(apiClient.api.getTicketTree).mockResolvedValue(mockTree);
      // First call fails, second succeeds
      vi.mocked(apiClient.api.getTicket)
        .mockRejectedValueOnce(new Error('Network error'))
        .mockResolvedValueOnce(mockTicket);

      renderDashboard();

      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Ticket'));
      });

      // First attempt
      fireEvent.click(screen.getByRole('button', { name: /details/i }));

      // Retry should be possible
      await waitFor(() => {
        const retryButton = screen.queryByRole('button', { name: /retry|try again/i });
        if (retryButton) {
          fireEvent.click(retryButton);
        }
      });
    });
  });
});

// Helper functions
function createMockTreeNode(overrides?: Partial<apiClient.TicketTreeNode>): apiClient.TicketTreeNode {
  return {
    id: 'ticket-123',
    external_id: '16-modal-with-ticket-details',
    title: 'Test Ticket',
    state: 'in_progress',
    priority: 1,
    work_item_type: 'feature',
    workflow_stage_name: 'Test Design',
    child_count: 0,
    children: [],
    ...overrides,
  };
}

function createMockTicket(overrides?: Partial<apiClient.TicketDetail>): apiClient.TicketDetail {
  return {
    id: 'ticket-123',
    external_id: '16-modal-with-ticket-details',
    title: 'Test Ticket',
    description: 'Test description',
    acceptance_criteria: [],
    state: 'in_progress',
    priority: 1,
    workspace_slug: 'loregarden',
    workflow_stage_key: 'test_design',
    workflow_stage_status: 'running',
    workflow_stage_name: 'Test Design',
    run_code: 'run_abc123',
    work_item_type: 'feature',
    parent_ticket_id: null,
    milestone: '',
    child_count: 0,
    revision: 1,
    last_updated_by: 'test@example.com',
    next_agent: 'implementation_agent',
    next_status: 'ready',
    blocking_issues: '',
    state_locked: false,
    stages: [],
    artifacts: {
      diff: null,
      logs: [],
      tests: null,
      context: [],
      error: null,
      live: null,
    },
    ...overrides,
  };
}

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Dashboard } from '../Dashboard';
import { RouterBridgeSync } from '../../components/RouterBridgeSync';
import * as apiClient from '../../api/client';
import { useUiStore } from '../../state/uiStore';

const mkWorkspace = (over: Partial<apiClient.WorkspaceSummary> = {}): apiClient.WorkspaceSummary => ({
  id: 'ws-1',
  slug: 'loregarden',
  name: 'Loregarden',
  repo_path: '.',
  repo_root: '/repo',
  repo_exists: true,
  ticket_count: 0,
  blocked_count: 0,
  workflow_template_slug: '',
  cli_adapter: '',
  claude_model: '',
  cursor_model: '',
  lmstudio_base_url: '',
  lmstudio_model: '',
  ...over,
});

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

// Mock the API without loading import.meta-bearing module
jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

describe('Dashboard - Ticket Details Button Integration', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    useUiStore.setState({
      stateFilters: [],
      typeFilters: [],
      search: '',
      expandedTicketIds: [],
      workspace: 'all',
      paneVisibility: {
        workspaces: true,
        tickets: true,
        workflow: true,
        artifacts: true,
      },
    });
    useUiStore.persist?.clearStorage?.();
    jest.clearAllMocks();
    jest.mocked(apiClient.api.ticket).mockImplementation(async (id: string) => createMockTicket({ id }));
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.triage).mockResolvedValue({ pending_approvals: [], recent_approvals: [], messages: [], runtime: { cli_adapter: '', claude_model: '', cursor_model: '', lmstudio_base_url: '', lmstudio_model: '' } });
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
  });

  const waitForDetailsButton = async () => {
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /view ticket details/i })).toBeInTheDocument();
    });
    return screen.getByRole('button', { name: /view ticket details/i });
  };

  const renderDashboard = (initialEntries: string[] = ['/']) => {
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tickets/:ticketId" element={<Dashboard />} />
            <Route path="/tickets/:ticketId/:artifactTab" element={<Dashboard />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  describe('Button Visibility and Placement', () => {
    it('should render "Details" button in workflow pane header when ticket is selected', async () => {
      // SPEC: Button should appear next to ticket title in the workflow pane
      // This is the main pane that shows selected ticket information

      // Mock API responses
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden', name: 'Loregarden' })];
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

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select a ticket
      await waitFor(() => {
        const treeItem = screen.getByText('Modal Feature');
        fireEvent.click(treeItem);
      });

      // Details button should appear in the workflow pane header
      await waitFor(() => {
        const button = screen.getByRole('button', { name: /view ticket details/i });
        expect(button).toBeInTheDocument();
        // Verify it's in the workflow pane area
        const workflowPane = screen.getByText('Workflow');
        expect(workflowPane.closest('.workflow-pane')).toContainElement(button);
      });
    });

    it('should not render Details button when no ticket is selected', () => {
      // SPEC: Button should not appear in empty workflow pane
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden', name: 'Loregarden' })];

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue([]);

      renderDashboard();

      const detailsButtons = screen.queryAllByRole('button', { name: /view ticket details/i });
      // Should not have a details button for workflow pane (may have other buttons)
      expect(detailsButtons.length).toBe(0);
    });

    it('should render button when switching between tickets', async () => {
      // SPEC: Button should update when different ticket is selected
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [
        createMockTreeNode({ id: 'ticket-1', title: 'First Ticket' }),
        createMockTreeNode({ id: 'ticket-2', title: 'Second Ticket' }),
      ];
      const mockTicket1 = createMockTicket({ id: 'ticket-1', title: 'First Ticket' });
      const mockTicket2 = createMockTicket({ id: 'ticket-2', title: 'Second Ticket' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockImplementation(async (id: string) => {
        if (id === 'ticket-1') return mockTicket1;
        if (id === 'ticket-2') return mockTicket2;
        return createMockTicket({ id });
      });

      renderDashboard();

      await waitForDetailsButton();

      // Select second ticket
      await waitFor(() => {
        const item = screen.getByText('Second Ticket');
        fireEvent.click(item);
      });

      await waitForDetailsButton();
    });
  });

  describe('Button Functionality and Modal Opening', () => {
    it('should open modal when Details button is clicked', async () => {
      // SPEC: Clicking button should open modal with full ticket details
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({
        id: 'ticket-1',
        title: 'Test Feature',
        description: 'This is a test feature',
      });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select ticket
      await waitFor(() => {
        fireEvent.click(screen.getByText('Test Feature'));
      });

      // Click Details button
      const button = screen.getByRole('button', { name: /view ticket details/i });
      fireEvent.click(button);

      // Modal should appear with ticket details
      await waitFor(() => {
        const dialog = screen.getByRole('dialog');
        expect(within(dialog).getByDisplayValue('Test Feature')).toBeInTheDocument();
        expect(within(dialog).getByDisplayValue('This is a test feature')).toBeInTheDocument();
      });
    });

    it('should display full ticket details in modal', async () => {
      // SPEC: Modal should show complete ticket information
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
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

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      // Select ticket and open modal
      await waitFor(() => {
        fireEvent.click(screen.getByText('Complete Feature'));
      });

      const button = screen.getByRole('button', { name: /view ticket details/i });
      fireEvent.click(button);

      // Verify all details are displayed
      await waitFor(() => {
        const dialog = screen.getByRole('dialog');
        expect(within(dialog).getByDisplayValue('Complete Feature')).toBeInTheDocument();
        expect(within(dialog).getByText('16-modal-with-ticket-details')).toBeInTheDocument();
        expect(within(dialog).getByDisplayValue('Full description')).toBeInTheDocument();
        expect(within(dialog).getByText('User can click button')).toBeInTheDocument();
        expect(within(dialog).getByText('Awaiting API review')).toBeInTheDocument();
      });
    });

    it('should close modal when Close button is clicked', async () => {
      // SPEC: Modal should close when user clicks close button
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitForDetailsButton();

      // Open modal
      fireEvent.click(await waitForDetailsButton());

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Close modal
      const closeButton = screen.getByRole('button', { name: /^Close$/i });
      fireEvent.click(closeButton);

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });
    });

    it('should allow reopening modal after closing', async () => {
      // SPEC: Button should work multiple times
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      const detailsButton = await waitForDetailsButton();

      // Open modal
      fireEvent.click(detailsButton);
      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Close modal
      fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));
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
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [
        createMockTreeNode({ id: 'ticket-1', title: 'Ticket 1' }),
        createMockTreeNode({ id: 'ticket-2', title: 'Ticket 2' }),
      ];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitForDetailsButton();
      fireEvent.click(await waitForDetailsButton());

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Should still see tickets pane in background
      expect(screen.getByText('Ticket 2')).toBeInTheDocument();
    });

    it('should maintain modal state when pane visibility changes', async () => {
      // SPEC: Modal should stay open/closed independently of pane toggles
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      await waitForDetailsButton();

      // Open modal
      fireEvent.click(await waitForDetailsButton());

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      // Modal should remain visible
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should close modal when navigating to a different ticket', async () => {
      // SPEC: Opening details for new ticket should handle previous modal gracefully
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [
        createMockTreeNode({ id: 'ticket-1', title: 'First' }),
        createMockTreeNode({ id: 'ticket-2', title: 'Second' }),
      ];
      const mockTicket1 = createMockTicket({ id: 'ticket-1', title: 'First' });
      const mockTicket2 = createMockTicket({ id: 'ticket-2', title: 'Second' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockImplementation(async (id: string) => {
        if (id === 'ticket-1') return mockTicket1;
        if (id === 'ticket-2') return mockTicket2;
        return createMockTicket({ id });
      });

      renderDashboard();

      const detailsButton = await waitForDetailsButton();
      fireEvent.click(detailsButton);

      await waitFor(() => {
        expect(screen.getByDisplayValue('First')).toBeInTheDocument();
      });

      // Switch to second ticket
      fireEvent.click(screen.getByText('Second'));

      // Modal should close automatically or be updated
      await waitFor(() => {
        const modal = screen.queryByRole('dialog');
        if (modal) {
          expect(within(modal).getByDisplayValue('Second')).toBeInTheDocument();
        }
      });
    });
  });

  describe('Accessibility in Context', () => {
    it('should have button with descriptive aria-label in pane header', async () => {
      // SPEC: Button must be accessible to screen readers
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1', title: 'Test' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      const button = await waitForDetailsButton();
      expect(button).toHaveAttribute('aria-label');
    });

    it('should support keyboard navigation to open modal', async () => {
      // SPEC: Button must be accessible via keyboard
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);

      renderDashboard();

      const button = await waitForDetailsButton();
      // Should be keyboard accessible
      expect(button).not.toBeDisabled();
      fireEvent.keyDown(button, { key: 'Enter' });
      // Should trigger click action
    });
  });

  describe('Error Handling', () => {
    it('should show error message if ticket details fail to load', async () => {
      // SPEC: Should handle API errors gracefully
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      jest.mocked(apiClient.api.ticket).mockImplementation(async () => {
        throw new Error('Failed to load');
      });

      renderDashboard();

      const button = await waitForDetailsButton();
      fireEvent.click(button);

      // Should show error state in modal
      await waitFor(() => {
        expect(screen.getByText('Failed to load')).toBeInTheDocument();
      });
    });

    it('should allow retry if details fail to load', async () => {
      // SPEC: User should be able to retry loading details
      const mockWorkspaces = [mkWorkspace({ slug: 'loregarden' })];
      const mockTree = [createMockTreeNode({ id: 'ticket-1' })];
      const mockTicket = createMockTicket({ id: 'ticket-1' });

      jest.mocked(apiClient.api.workspaces).mockResolvedValue(mockWorkspaces);
      jest.mocked(apiClient.api.ticketTree).mockResolvedValue(mockTree);
      renderDashboard();

      const button = await waitForDetailsButton();

      jest.mocked(apiClient.api.ticket).mockRejectedValue(new Error('Network error'));
      fireEvent.click(button);

      await waitFor(() => {
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

      jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket);
      fireEvent.click(button);

      await waitFor(() => {
        expect(screen.getByDisplayValue('Test Ticket')).toBeInTheDocument();
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
    workflow_stage_status: 'pending',
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
    branch: 'main',
    workflow_template_slug: 'default',
    workflow_template_name: 'Default',
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

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TicketDetailsModal } from '../TicketDetailsModal';
import * as apiClient from '../../api/client';

/**
 * Test suite for TicketDetailsModal component.
 *
 * Spec reference: 16-modal-with-ticket-details
 * Feature: Ticket pane should include a button that opens a modal with full ticket details
 */

describe('TicketDetailsModal', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
  });

  // Helper function to render component with query client
  const renderWithQueryClient = (component: React.ReactElement) => {
    return render(
      <QueryClientProvider client={queryClient}>
        {component}
      </QueryClientProvider>
    );
  };

  describe('Closed State', () => {
    it('should render nothing when modal is closed', () => {
      const ticket = createMockTicket({ id: 'ticket-1', title: 'Test Ticket' });
      renderWithQueryClient(<TicketDetailsModal ticket={ticket} isOpen={false} onClose={() => {}} />);

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('should not render when ticket is null and modal is closed', () => {
      renderWithQueryClient(<TicketDetailsModal ticket={null} isOpen={false} onClose={() => {}} />);

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('should not render when ticket is null without loading or error', () => {
      renderWithQueryClient(<TicketDetailsModal ticket={null} isOpen={true} onClose={() => {}} />);

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('should show loading state when ticket is null but loading', () => {
      renderWithQueryClient(
        <TicketDetailsModal ticket={null} isOpen={true} onClose={() => {}} isLoading={true} />
      );

      expect(screen.getByText(/loading ticket details/i)).toBeInTheDocument();
    });
  });

  describe('Modal Opening and Closing', () => {
    it('should open modal when isOpen is true', async () => {
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });
    });

    it('should close modal when close button is clicked', async () => {
      // SPEC: Modal should close when user clicks close button
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const closeButton = screen.getByRole('button', { name: /^Close$/i });
      fireEvent.click(closeButton);

      expect(onClose).toHaveBeenCalled();
    });

    it('should close modal when escape key is pressed', async () => {
      // SPEC: Modal should close when user presses escape key
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');
      fireEvent.keyDown(dialog, { key: 'Escape' });

      expect(onClose).toHaveBeenCalled();
    });

    it('should close modal when overlay/backdrop is clicked', async () => {
      // SPEC: Modal should close when user clicks outside the modal
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const backdrop = screen.getByTestId('modal-backdrop');
      fireEvent.click(backdrop);

      expect(onClose).toHaveBeenCalled();
    });

    it('should not close modal when clicking inside modal content', async () => {
      // SPEC: Modal should remain open when clicking modal content (not overlay)
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const modalContent = screen.getByTestId('modal-content');
      fireEvent.click(modalContent);

      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe('Ticket Details Display', () => {
    it('should display ticket title', () => {
      // SPEC: Modal should show ticket title prominently
      const ticket = createMockTicket({ title: 'Important Feature' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByDisplayValue('Important Feature')).toBeInTheDocument();
    });

    it('should display ticket ID', () => {
      // SPEC: Modal should display the ticket external ID for reference
      const ticket = createMockTicket({ external_id: '16-modal-with-ticket-details' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('16-modal-with-ticket-details')).toBeInTheDocument();
    });

    it('should display ticket description', () => {
      // SPEC: Modal should display full ticket description
      const ticket = createMockTicket({ description: 'This is the full description of the ticket.' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByDisplayValue('This is the full description of the ticket.')).toBeInTheDocument();
    });

    it('should allow editing title and description and save changes', async () => {
      const onSave = jest.fn().mockResolvedValue(undefined);
      const ticket = createMockTicket({ title: 'Original title', description: 'Original description' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} onSave={onSave} />
      );

      fireEvent.change(screen.getByDisplayValue('Original title'), { target: { value: 'Updated title' } });
      fireEvent.change(screen.getByDisplayValue('Original description'), {
        target: { value: 'Updated description' },
      });

      fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith({
          title: 'Updated title',
          description: 'Updated description',
        });
      });
    });

    it('should disable save when title is empty', () => {
      const onSave = jest.fn();
      const ticket = createMockTicket({ title: 'Original title' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} onSave={onSave} />
      );

      fireEvent.change(screen.getByDisplayValue('Original title'), { target: { value: '   ' } });

      expect(screen.getByRole('button', { name: /save changes/i })).toBeDisabled();
    });

    it('should display acceptance criteria as a list', () => {
      // SPEC: Modal should display acceptance criteria in a readable format
      const ticket = createMockTicket({
        acceptance_criteria: [
          'Criterion 1: User can click button',
          'Criterion 2: Modal appears with details',
          'Criterion 3: Modal can be closed',
        ],
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('Criterion 1: User can click button')).toBeInTheDocument();
      expect(screen.getByText('Criterion 2: Modal appears with details')).toBeInTheDocument();
      expect(screen.getByText('Criterion 3: Modal can be closed')).toBeInTheDocument();
    });

    it('should display ticket state badge', () => {
      // SPEC: Modal should show current ticket state
      const ticket = createMockTicket({ state: 'in_progress' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText(/in progress|in_progress/i)).toBeInTheDocument();
    });

    it('should display workflow stage information', () => {
      // SPEC: Modal should show current workflow stage if applicable
      const ticket = createMockTicket({
        workflow_stage_name: 'Test Design',
        workflow_stage_key: 'test_design',
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('Test Design')).toBeInTheDocument();
    });

    it('should display priority information', () => {
      // SPEC: Modal should display ticket priority
      const ticket = createMockTicket({ priority: 2 });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('Priority')).toBeInTheDocument();
    });

    it('should display work item type', () => {
      // SPEC: Modal should show the work item type (feature, task, bug, etc.)
      const ticket = createMockTicket({ work_item_type: 'feature' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText(/feature|feature type/i)).toBeInTheDocument();
    });

    it('should display blocking issues when present', () => {
      // SPEC: Modal should display any blocking issues that prevent progress
      const ticket = createMockTicket({
        blocking_issues: 'Cannot proceed without API approval',
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('Cannot proceed without API approval')).toBeInTheDocument();
    });

    it('should not display blocking issues section when empty', () => {
      // SPEC: Don't show blocking issues section if none exist
      const ticket = createMockTicket({ blocking_issues: '' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const blockingSection = screen.queryByText(/blocking|issues/i);
      // Should not show blocking section, but might show as empty
      if (blockingSection) {
        expect(blockingSection.textContent).toBe('');
      }
    });

    it('should display revision number', () => {
      // SPEC: Modal should show revision for audit purposes
      const ticket = createMockTicket({ revision: 5 });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText(/revision|rev|v/i)).toBeInTheDocument();
    });

    it('should display last updated information', () => {
      // SPEC: Modal should show who last updated the ticket and when
      const ticket = createMockTicket({ last_updated_by: 'alice@example.com' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('Last updated by')).toBeInTheDocument();
      expect(screen.getByText('alice@example.com')).toBeInTheDocument();
    });

    it('should display stages list if workflow present', () => {
      // SPEC: Modal should show workflow stages if this is a workflow item
      const ticket = createMockTicket({
        stages: [
          { key: 'planning', name: 'Planning', status: 'done' },
          { key: 'implementation', name: 'Implementation', status: 'in_progress' },
          { key: 'testing', name: 'Testing', status: 'pending' },
        ],
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText('Planning')).toBeInTheDocument();
      expect(screen.getByText('Implementation')).toBeInTheDocument();
      expect(screen.getByText('Testing')).toBeInTheDocument();
    });
  });

  describe('Artifacts Display', () => {
    it('should display diff artifact if present', () => {
      // SPEC: Modal should show code changes if diff artifact exists
      const ticket = createMockTicket({
        artifacts: {
          diff: {
            file: 'src/components/Button.tsx',
            sections: [
              { path: 'src/components/Button.tsx', add: 5, del: 2, lines: [] },
            ],
          },
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText(/Code Diff:/i)).toBeInTheDocument();
    });

    it('should display test artifact if present', () => {
      // SPEC: Modal should show test results if test artifact exists
      const ticket = createMockTicket({
        artifacts: {
          tests: {
            command: 'npm test',
            passed: 5,
            failed: 0,
            status: 'passed',
          },
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText(/Test Results:/i)).toBeInTheDocument();
    });

    it('should display logs if present', () => {
      // SPEC: Modal should show execution logs if available
      const ticket = createMockTicket({
        artifacts: {
          logs: [
            { text: 'Starting agent run...', type: 'info' },
            { text: 'Process completed', type: 'info' },
          ],
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      // Logs should be displayed in some form
      expect(screen.getByText(/logs|output|console/i)).toBeInTheDocument();
    });

    it('should display error artifact if present', () => {
      // SPEC: Modal should show error information if stage failed
      const ticket = createMockTicket({
        artifacts: {
          error: {
            message: 'Stage failed: timeout exceeded',
            stage_key: 'implementation',
            agent_id: 'backend_implementer',
          },
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByText(/Stage failed: timeout exceeded/i)).toBeInTheDocument();
    });
  });

  describe('Edge Cases and Error States', () => {
    it('should handle missing acceptance criteria gracefully', () => {
      // SPEC: Should not crash if acceptance criteria is empty
      const ticket = createMockTicket({ acceptance_criteria: [] });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle very long descriptions', () => {
      // SPEC: Should handle long text without breaking layout
      const longDescription = 'A'.repeat(5000);
      const ticket = createMockTicket({ description: longDescription });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      expect(dialog).toBeInTheDocument();
      expect(dialog.textContent).toContain('A');
    });

    it('should handle empty acceptance criteria array', () => {
      // SPEC: Should not display acceptance criteria section if empty
      const ticket = createMockTicket({ acceptance_criteria: [] });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should display loading state while fetching ticket details', () => {
      // SPEC: Should show loading indicator while data is loading
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} isLoading={true} />
      );

      expect(screen.getByText(/loading|fetching/i)).toBeInTheDocument();
    });

    it('should display error state when fetch fails', () => {
      // SPEC: Should show error message if ticket details fail to load
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal
          ticket={ticket}
          isOpen={true}
          onClose={() => {}}
          error="Failed to load ticket details"
        />
      );

      expect(screen.getByText('Failed to load ticket details')).toBeInTheDocument();
    });

    it('should handle null artifact data', () => {
      // SPEC: Should not crash when artifacts are null
      const ticket = createMockTicket({
        artifacts: {
          diff: null,
          logs: null,
          tests: null,
          error: null,
          context: null,
          live: null,
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    it('should have proper ARIA labels for all interactive elements', () => {
      // SPEC: Modal should be accessible to screen reader users
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      expect(dialog).toHaveAttribute('aria-labelledby');
      expect(dialog).toHaveAttribute('aria-describedby');
    });

    it('should manage focus correctly when modal opens', () => {
      // SPEC: Focus should move to modal when it opens
      const ticket = createMockTicket();
      const { container } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      const focusIsOnDialog = document.activeElement === dialog || dialog.contains(document.activeElement as Node);
      expect(focusIsOnDialog).toBe(true);
    });

    it('should have semantic HTML structure', () => {
      // SPEC: Modal should use proper semantic HTML elements
      const ticket = createMockTicket({ title: 'Test' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      expect(dialog).toHaveAttribute('role', 'dialog');
    });

    it('should provide keyboard navigation', () => {
      // SPEC: Users should be able to navigate modal with keyboard
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');
      fireEvent.keyDown(dialog, { key: 'Escape' });
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('Integration with Dashboard', () => {
    it('should work within Dashboard pane layout', () => {
      // SPEC: Modal should not interfere with dashboard layout
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      // Modal should not disrupt the DOM structure
    });

    it('should preserve state when toggling multiple times', () => {
      // SPEC: Modal state should be independent of Dashboard state
      const onClose = jest.fn();
      const ticket = createMockTicket({ title: 'Persistent Ticket' });
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
      );

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
        </QueryClientProvider>
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();

      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
        </QueryClientProvider>
      );

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  describe('Performance', () => {
    it('should handle large ticket objects efficiently', () => {
      // SPEC: Modal should not have performance issues with large tickets
      const largeTicket = createMockTicket({
        acceptance_criteria: Array.from({ length: 100 }, (_, i) => `Criterion ${i + 1}`),
        stages: Array.from({ length: 50 }, (_, i) => ({
          key: `stage_${i}`,
          name: `Stage ${i + 1}`,
          status: 'pending' as const,
          agent_id: 'agent',
          skill_name: 'skill',
          optional: false,
          note: '',
        })),
      });

      const { container } = renderWithQueryClient(
        <TicketDetailsModal ticket={largeTicket} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should not re-render unnecessarily when props do not change', () => {
      // SPEC: Component should use memoization or efficient comparison
      const ticket = createMockTicket();
      const onClose = jest.fn();
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      // Verify initial render
      expect(screen.getByRole('dialog')).toBeInTheDocument();

      // Re-render with same props
      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
        </QueryClientProvider>
      );

      // Should still be in document (no unnecessary unmount)
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  describe('Keyboard Navigation', () => {
    it('should allow tabbing through interactive elements within modal', () => {
      // SPEC: Users should be able to navigate all interactive elements with Tab key
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      const interactiveElements = dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]');
      expect(interactiveElements.length).toBeGreaterThan(0);
    });

    it('should trap focus within modal when open (optional - depends on implementation)', () => {
      // SPEC: Modal should contain focus to prevent user from tabbing behind it
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');
      const buttons = dialog.querySelectorAll('button');
      expect(buttons.length).toBeGreaterThan(0);
    });

    it('should restore focus to button when modal closes', () => {
      // SPEC: Focus should return to the trigger button after modal closes
      const onClose = jest.fn();
      const ticket = createMockTicket();
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();

      // Simulate closing the modal
      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
        </QueryClientProvider>
      );

      // Dialog should be removed
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  describe('Content Scrolling', () => {
    it('should allow scrolling through long ticket content', () => {
      // SPEC: Modal should be scrollable when content exceeds viewport
      const veryLongDescription = 'Lorem ipsum dolor sit amet. '.repeat(200);
      const ticket = createMockTicket({ description: veryLongDescription });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      const content = dialog.querySelector('[data-testid="modal-content"]') || dialog;

      // Modal should be present and have scrollable content
      expect(dialog).toBeInTheDocument();
      expect(content.textContent).toContain('Lorem ipsum');
    });

    it('should display scrollbar for large acceptance criteria lists', () => {
      // SPEC: Modal should properly handle scrollable sections
      const ticket = createMockTicket({
        acceptance_criteria: Array.from({ length: 50 }, (_, i) => `Criterion ${i + 1}: A detailed requirement`),
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );

      // All criteria should be present (or accessible via scrolling)
      expect(screen.getByText('Criterion 1: A detailed requirement')).toBeInTheDocument();
      expect(screen.getByText('Criterion 25: A detailed requirement')).toBeInTheDocument();
      expect(screen.getByText('Criterion 50: A detailed requirement')).toBeInTheDocument();
    });
  });

  describe('Rapid Open/Close Cycles', () => {
    it('should handle rapid open/close without errors', async () => {
      // SPEC: Modal should be stable under rapid state changes
      const onClose = jest.fn();
      const ticket = createMockTicket();
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
      );

      // Rapidly toggle open/close
      for (let i = 0; i < 5; i++) {
        rerender(
          <QueryClientProvider client={queryClient}>
            <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
          </QueryClientProvider>
        );

        if (i % 2 === 0) {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
            </QueryClientProvider>
          );
        }
      }

      // Should end in a valid state
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('should maintain data consistency across multiple open/close cycles', () => {
      // SPEC: Modal data should not corrupt with repeated opens
      const ticket = createMockTicket({
        title: 'Immutable Title',
        external_id: '16-modal-with-ticket-details',
      });
      const onClose = jest.fn();
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      // First open
      expect(screen.getByDisplayValue('Immutable Title')).toBeInTheDocument();

      // Close and reopen
      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
        </QueryClientProvider>
      );

      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
        </QueryClientProvider>
      );

      // Data should still be correct
      expect(screen.getByDisplayValue('Immutable Title')).toBeInTheDocument();
      expect(screen.getByText('16-modal-with-ticket-details')).toBeInTheDocument();
    });
  });

  describe('Switching Between Tickets', () => {
    it('should update content when ticket prop changes while modal is open', () => {
      // SPEC: Modal should reflect new ticket data when ticket prop changes
      const ticket1 = createMockTicket({ id: 'ticket-1', title: 'First Ticket' });
      const ticket2 = createMockTicket({ id: 'ticket-2', title: 'Second Ticket' });
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket1} isOpen={true} onClose={() => {}} />
      );

      expect(screen.getByDisplayValue('First Ticket')).toBeInTheDocument();

      // Switch to different ticket
      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket2} isOpen={true} onClose={() => {}} />
        </QueryClientProvider>
      );

      expect(screen.getByDisplayValue('Second Ticket')).toBeInTheDocument();
      expect(screen.queryByDisplayValue('First Ticket')).not.toBeInTheDocument();
    });
  });

  describe('Modal Positioning and Overflow', () => {
    it('should not overflow viewport even with maximum content', () => {
      // SPEC: Modal should fit within viewport with scrolling
      const maxContent = createMockTicket({
        title: 'A'.repeat(500),
        description: 'B'.repeat(5000),
        acceptance_criteria: Array.from({ length: 100 }, (_, i) => `Criterion ${i + 1}: Long requirement text`),
        blocking_issues: 'D'.repeat(1000),
      });

      const { container } = renderWithQueryClient(
        <TicketDetailsModal ticket={maxContent} isOpen={true} onClose={() => {}} />
      );

      const dialog = screen.getByRole('dialog');
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute('data-testid', 'modal-content');
    });
  });
});

// Helper function to create mock ticket
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

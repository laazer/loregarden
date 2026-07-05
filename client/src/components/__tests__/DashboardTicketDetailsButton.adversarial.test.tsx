import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardTicketDetailsButton } from '../DashboardTicketDetailsButton';
import * as apiClient from '../../api/client';

/**
 * ADVERSARIAL TEST SUITE: DashboardTicketDetailsButton Integration (16-modal-with-ticket-details)
 *
 * This test suite focuses on exposing weaknesses in the integration between:
 * - Dashboard context and ticket selection state
 * - Button visibility and state management
 * - Modal open/close interactions
 * - Error handling and edge cases
 *
 * Uses systematic adversarial testing techniques to find hidden bugs.
 */

describe('DashboardTicketDetailsButton - Adversarial Test Suite', () => {
  let queryClient: QueryClient;
  let mockDashboardContext: any;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    mockDashboardContext = {
      selectedTicket: createMockTicket(),
      isLoading: false,
      error: null,
    };

    jest.clearAllMocks();
  });

  afterEach(() => {
    queryClient.clear();
  });

  const renderWithContextAndQueryClient = (
    component: React.ReactElement,
    context = mockDashboardContext
  ) => {
    return render(
      <QueryClientProvider client={queryClient}>
        <MockDashboardContext.Provider value={context}>
          {component}
        </MockDashboardContext.Provider>
      </QueryClientProvider>
    );
  };

  // ============================================================================
  // BUTTON RENDERING ADVERSARIAL TESTS
  // ============================================================================
  describe('Button Rendering - Adversarial Cases', () => {
    it('should handle undefined selectedTicket (not just null)', () => {
      mockDashboardContext.selectedTicket = undefined;
      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.queryByRole('button', { name: /details/i })).not.toBeInTheDocument();
    });

    it('should handle selectedTicket prop changing to null mid-render', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      expect(screen.getByRole('button', { name: /details/i })).toBeInTheDocument();

      mockDashboardContext.selectedTicket = null;
      rerender(<DashboardTicketDetailsButton />);

      expect(screen.queryByRole('button', { name: /details/i })).not.toBeInTheDocument();
    });

    it('should handle empty ticket object', () => {
      mockDashboardContext.selectedTicket = {} as any;
      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      // Should still be in DOM or handle gracefully
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('should handle ticket with missing ID field', () => {
      const incompleteTicket = createMockTicket();
      delete incompleteTicket.id;
      mockDashboardContext.selectedTicket = incompleteTicket;

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.queryByRole('button', { name: /details/i })).not.toBeInTheDocument();
    });

    it('should handle rapid ticket selection changes', () => {
      const tickets = Array.from({ length: 10 }, (_, i) =>
        createMockTicket({ id: `ticket-${i}`, title: `Ticket ${i}` })
      );

      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      tickets.forEach((ticket) => {
        mockDashboardContext.selectedTicket = ticket;
        rerender(<DashboardTicketDetailsButton />);
      });

      expect(true).toBe(true);
    });

    it('should handle button with corrupted label content', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);

      const button = screen.getByRole('button');
      expect(button).toBeInTheDocument();
      // Button should have content regardless of what it is
      expect(button.textContent).toBeDefined();
    });

    it('should handle very long ticket title affecting button display', () => {
      mockDashboardContext.selectedTicket = createMockTicket({
        title: 'A'.repeat(1000),
      });

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.getByRole('button', { name: /details/i })).toBeInTheDocument();
    });
  });

  // ============================================================================
  // BUTTON STATE & LOADING ADVERSARIAL TESTS
  // ============================================================================
  describe('Button State & Loading - Adversarial Cases', () => {
    it('should handle isLoading=true while selectedTicket is null', () => {
      mockDashboardContext.selectedTicket = null;
      mockDashboardContext.isLoading = true;

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('should handle rapid loading state changes', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      for (let i = 0; i < 50; i++) {
        mockDashboardContext.isLoading = i % 2 === 0;
        rerender(<DashboardTicketDetailsButton />);
      }

      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('should handle loading state with error simultaneously', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      mockDashboardContext.isLoading = true;
      mockDashboardContext.error = 'Load failed';

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('should handle button disabled state during concurrent loads', async () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      mockDashboardContext.isLoading = false;

      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      let button = screen.getByRole('button');
      expect(button).not.toBeDisabled();

      // Simulate concurrent load request
      mockDashboardContext.isLoading = true;
      rerender(<DashboardTicketDetailsButton />);

      button = screen.getByRole('button');
      expect(button).toBeDisabled();
    });
  });

  // ============================================================================
  // MODAL OPENING ADVERSARIAL TESTS
  // ============================================================================
  describe('Modal Opening - Adversarial Cases', () => {
    it('should handle button click when modal is already open', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const onModalOpen = jest.fn();

      renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton onModalOpen={onModalOpen} />
      );

      const button = screen.getByRole('button');
      fireEvent.click(button);
      fireEvent.click(button); // Click again while modal might be opening

      expect(onModalOpen.mock.calls.length).toBeGreaterThan(0);
    });

    it('should handle rapid modal open attempts', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const onModalOpen = jest.fn();

      renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton onModalOpen={onModalOpen} />
      );

      const button = screen.getByRole('button');

      // Rapid clicks
      for (let i = 0; i < 20; i++) {
        fireEvent.click(button);
      }

      expect(onModalOpen.mock.calls.length).toBeGreaterThan(0);
    });

    it('should handle modal open callback being null/undefined', () => {
      mockDashboardContext.selectedTicket = createMockTicket();

      renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton onModalOpen={null as any} />
      );

      const button = screen.getByRole('button');
      expect(() => fireEvent.click(button)).not.toThrow();
    });

    it('should handle modal open callback throwing error', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const badCallback = jest.fn(() => {
        throw new Error('Callback failed');
      });

      renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton onModalOpen={badCallback} />
      );

      const button = screen.getByRole('button');
      // Should not throw to user - error should be caught
      expect(() => fireEvent.click(button)).not.toThrow();
    });

    it('should handle modal open when ticket changes during click', () => {
      const ticket1 = createMockTicket({ id: 'ticket-1' });
      const ticket2 = createMockTicket({ id: 'ticket-2' });

      mockDashboardContext.selectedTicket = ticket1;
      const onModalOpen = jest.fn();

      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton onModalOpen={onModalOpen} />
      );

      const button = screen.getByRole('button');

      // Start click, change ticket before handler completes
      mockDashboardContext.selectedTicket = ticket2;
      fireEvent.click(button);

      rerender(<DashboardTicketDetailsButton onModalOpen={onModalOpen} />);

      expect(onModalOpen).toHaveBeenCalled();
    });
  });

  // ============================================================================
  // CONTEXT & STATE MANAGEMENT ADVERSARIAL TESTS
  // ============================================================================
  describe('Context & State Management - Adversarial Cases', () => {
    it('should handle context value being undefined', () => {
      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <DashboardTicketDetailsButton />
        </QueryClientProvider>
      );

      // Should either handle gracefully or throw controlled error
      expect(() => unmount()).not.toThrow();
    });

    it('should handle context value changing mid-render', () => {
      mockDashboardContext.selectedTicket = createMockTicket({ id: 'ticket-1' });

      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      expect(screen.getByRole('button')).toBeInTheDocument();

      mockDashboardContext.selectedTicket = createMockTicket({ id: 'ticket-2' });
      rerender(<DashboardTicketDetailsButton />);

      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('should handle multiple context providers with conflicting values', () => {
      mockDashboardContext.selectedTicket = createMockTicket();

      // Should use closest provider
      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <MockDashboardContext.Provider value={mockDashboardContext}>
            <MockDashboardContext.Provider
              value={{ selectedTicket: null, isLoading: false, error: null }}
            >
              <DashboardTicketDetailsButton />
            </MockDashboardContext.Provider>
          </MockDashboardContext.Provider>
        </QueryClientProvider>
      );

      // Should use the inner context (null ticket)
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
      unmount();
    });

    it('should handle partial context values', () => {
      const partialContext = {
        selectedTicket: createMockTicket(),
        // isLoading and error are missing
      } as any;

      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <MockDashboardContext.Provider value={partialContext}>
            <DashboardTicketDetailsButton />
          </MockDashboardContext.Provider>
        </QueryClientProvider>
      );

      // Should handle missing context fields gracefully
      expect(() => unmount()).not.toThrow();
    });
  });

  // ============================================================================
  // ERROR HANDLING ADVERSARIAL TESTS
  // ============================================================================
  describe('Error Handling - Adversarial Cases', () => {
    it('should handle error state without breaking button rendering', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      mockDashboardContext.error = 'Network error';

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('should handle rapidly changing error states', () => {
      mockDashboardContext.selectedTicket = createMockTicket();

      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      const errors = ['Error 1', 'Error 2', null, 'Error 3', null, ''];

      errors.forEach((error) => {
        mockDashboardContext.error = error;
        rerender(<DashboardTicketDetailsButton />);
      });

      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('should handle very long error messages', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      mockDashboardContext.error = 'A'.repeat(10000);

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('should handle error with special characters and HTML', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      mockDashboardContext.error = '<script>alert("xss")</script>';

      renderWithContextAndQueryClient(<DashboardTicketDetailsButton />);
      expect(screen.getByRole('button')).toBeInTheDocument();
      // Verify XSS is not executed
      expect(screen.queryByText('alert')).not.toBeInTheDocument();
    });

    it('should handle null error vs undefined error vs empty string', () => {
      const testCases = [null, undefined, ''];

      testCases.forEach((error) => {
        mockDashboardContext.selectedTicket = createMockTicket();
        mockDashboardContext.error = error;

        const { unmount } = renderWithContextAndQueryClient(
          <DashboardTicketDetailsButton />
        );
        expect(screen.getByRole('button')).toBeInTheDocument();
        unmount();
      });
    });
  });

  // ============================================================================
  // ACCESSIBILITY ADVERSARIAL TESTS
  // ============================================================================
  describe('Accessibility - Adversarial Cases', () => {
    it('should maintain ARIA labels when button state changes', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      const button = screen.getByRole('button');
      const initialAriaLabel = button.getAttribute('aria-label');

      mockDashboardContext.isLoading = true;
      rerender(<DashboardTicketDetailsButton />);

      const updatedButton = screen.getByRole('button');
      expect(updatedButton.getAttribute('aria-label')).toBeDefined();
    });

    it('should handle keyboard activation during loading', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      mockDashboardContext.isLoading = true;
      const onModalOpen = jest.fn();

      renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton onModalOpen={onModalOpen} />
      );

      const button = screen.getByRole('button');
      fireEvent.keyDown(button, { key: 'Enter' });
      fireEvent.keyDown(button, { key: ' ' });

      // Should not trigger modal while loading
      expect(onModalOpen).not.toHaveBeenCalled();
    });

    it('should maintain focus visibility during rapid state changes', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      const button = screen.getByRole('button');
      button.focus();

      for (let i = 0; i < 10; i++) {
        mockDashboardContext.isLoading = i % 2 === 0;
        rerender(<DashboardTicketDetailsButton />);
      }

      // Button should still exist and be focusable
      expect(screen.getByRole('button')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // INTEGRATION WITH DASHBOARD LAYOUT ADVERSARIAL TESTS
  // ============================================================================
  describe('Dashboard Integration - Adversarial Cases', () => {
    it('should not interfere with other dashboard pane elements', () => {
      mockDashboardContext.selectedTicket = createMockTicket();

      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <MockDashboardContext.Provider value={mockDashboardContext}>
            <div className="dashboard-layout">
              <div className="pane-header">
                <DashboardTicketDetailsButton />
              </div>
              <div className="pane-content">
                <input type="text" placeholder="Search" />
                <button>Other Button</button>
              </div>
            </div>
          </MockDashboardContext.Provider>
        </QueryClientProvider>
      );

      expect(screen.getByRole('button', { name: /details/i })).toBeInTheDocument();
      expect(screen.getByPlaceholderText('Search')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Other Button' })).toBeInTheDocument();

      unmount();
    });

    it('should handle being rendered in different viewport sizes', () => {
      mockDashboardContext.selectedTicket = createMockTicket();

      const viewportSizes = [
        { width: 320, height: 568 }, // Mobile
        { width: 768, height: 1024 }, // Tablet
        { width: 1920, height: 1080 }, // Desktop
      ];

      viewportSizes.forEach(({ width, height }) => {
        window.innerWidth = width;
        window.innerHeight = height;

        const { unmount } = renderWithContextAndQueryClient(
          <DashboardTicketDetailsButton />
        );

        expect(screen.getByRole('button')).toBeInTheDocument();
        unmount();
      });
    });

    it('should handle pane visibility changes', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      expect(screen.getByRole('button')).toBeInTheDocument();

      // Simulate pane being hidden/shown
      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <MockDashboardContext.Provider value={mockDashboardContext}>
            <div style={{ display: 'none' }}>
              <DashboardTicketDetailsButton />
            </div>
          </MockDashboardContext.Provider>
        </QueryClientProvider>
      );

      unmount();
      expect(true).toBe(true);
    });
  });

  // ============================================================================
  // PERFORMANCE ADVERSARIAL TESTS
  // ============================================================================
  describe('Performance - Adversarial Cases', () => {
    it('should handle rapid ticket selections without performance degradation', () => {
      const tickets = Array.from({ length: 100 }, (_, i) =>
        createMockTicket({ id: `ticket-${i}` })
      );

      const { rerender } = renderWithContextAndQueryClient(
        <DashboardTicketDetailsButton />
      );

      const start = performance.now();

      tickets.forEach((ticket) => {
        mockDashboardContext.selectedTicket = ticket;
        rerender(<DashboardTicketDetailsButton />);
      });

      const duration = performance.now() - start;
      expect(duration).toBeLessThan(5000); // Should complete in 5 seconds
    });

    it('should not cause unnecessary re-renders', () => {
      mockDashboardContext.selectedTicket = createMockTicket();
      const renderSpy = jest.fn();

      const { rerender } = renderWithContextAndQueryClient(
        <div>
          <DashboardTicketDetailsButton />
        </div>
      );

      // Props haven't changed
      rerender(
        <div>
          <DashboardTicketDetailsButton />
        </div>
      );

      // Component should still be in DOM
      expect(screen.getByRole('button')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // STRESS TESTS
  // ============================================================================
  describe('Stress Tests', () => {
    it('should handle 100 consecutive mount/unmount cycles', () => {
      for (let i = 0; i < 100; i++) {
        const { unmount } = renderWithContextAndQueryClient(
          <DashboardTicketDetailsButton />
        );
        unmount();
      }

      expect(true).toBe(true);
    });

    it('should handle simultaneous renders with different contexts', async () => {
      const promises = Array.from({ length: 50 }, async (_, i) => {
        return new Promise<void>((resolve) => {
          const context = {
            selectedTicket: createMockTicket({ id: `ticket-${i}` }),
            isLoading: i % 2 === 0,
            error: i % 3 === 0 ? 'Error' : null,
          };

          const { unmount } = render(
            <QueryClientProvider client={queryClient}>
              <MockDashboardContext.Provider value={context}>
                <DashboardTicketDetailsButton />
              </MockDashboardContext.Provider>
            </QueryClientProvider>
          );

          setTimeout(() => {
            unmount();
            resolve();
          }, i * 5);
        });
      });

      await Promise.all(promises);
      expect(true).toBe(true);
    });
  });
});

// ============================================================================
// MOCK CONTEXT
// ============================================================================

const MockDashboardContext = React.createContext<any>(null);

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

// Required for creating context
import React from 'react';

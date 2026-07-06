import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardTicketDetailsButton } from '../DashboardTicketDetailsButton';
import * as apiClient from '../../api/client';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

describe('DashboardTicketDetailsButton - Adversarial Test Suite', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    jest.clearAllMocks();
  });

  afterEach(() => {
    queryClient.clear();
  });

  const renderButton = (ticketId = 'ticket-123') =>
    render(
      <QueryClientProvider client={queryClient}>
        <DashboardTicketDetailsButton ticketId={ticketId} />
      </QueryClientProvider>
    );

  describe('Button Rendering - Adversarial Cases', () => {
    it('should always render a details button for a valid ticket id', () => {
      renderButton();
      expect(screen.getByRole('button', { name: /view ticket details/i })).toBeInTheDocument();
    });

    it('should handle rapid mount/unmount cycles', () => {
      for (let i = 0; i < 20; i++) {
        const { unmount } = renderButton(`ticket-${i}`);
        unmount();
      }
      expect(true).toBe(true);
    });

    it('should handle empty ticket id string', () => {
      renderButton('');
      expect(screen.getByRole('button', { name: /view ticket details/i })).toBeInTheDocument();
    });
  });

  describe('Modal Opening - Adversarial Cases', () => {
    it('should handle rapid modal open attempts', async () => {
      jest.mocked(apiClient.api.ticket).mockResolvedValue(createMockTicket());
      renderButton();

      const button = screen.getByRole('button', { name: /view ticket details/i });
      for (let i = 0; i < 20; i++) {
        fireEvent.click(button);
      }

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });
    });

    it('should handle modal open when ticket fetch fails', async () => {
      jest.mocked(apiClient.api.ticket).mockRejectedValue(new Error('Network error'));
      renderButton();

      fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));

      await waitFor(() => {
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });
    });

    it('should handle ticket id changing between opens', async () => {
      jest.mocked(apiClient.api.ticket)
        .mockResolvedValueOnce(createMockTicket({ id: 'ticket-1', title: 'Ticket 1' }))
        .mockResolvedValueOnce(createMockTicket({ id: 'ticket-2', title: 'Ticket 2' }));

      const { rerender } = render(
        <QueryClientProvider client={queryClient}>
          <DashboardTicketDetailsButton ticketId="ticket-1" />
        </QueryClientProvider>
      );

      fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));
      await waitFor(() => {
        expect(screen.getByDisplayValue('Ticket 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));
      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });

      rerender(
        <QueryClientProvider client={queryClient}>
          <DashboardTicketDetailsButton ticketId="ticket-2" />
        </QueryClientProvider>
      );

      fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));
      await waitFor(() => {
        expect(screen.getByDisplayValue('Ticket 2')).toBeInTheDocument();
      });
    });
  });

  describe('Loading and Error States', () => {
    it('should disable button while ticket details are loading', async () => {
      let resolveTicket: (value: apiClient.TicketDetail) => void = () => {};
      jest.mocked(apiClient.api.ticket).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveTicket = resolve;
          })
      );

      renderButton();
      fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view ticket details/i })).toBeDisabled();
      });

      resolveTicket(createMockTicket());
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view ticket details/i })).not.toBeDisabled();
      });
    });

    it('should show corrupted ticket data without crashing', async () => {
      jest.mocked(apiClient.api.ticket).mockResolvedValue(
        createMockTicket({
          title: 123456 as unknown as string,
          acceptance_criteria: 'Not an array' as unknown as string[],
        })
      );

      renderButton();
      fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByDisplayValue('123456')).toBeInTheDocument();
      });
    });
  });

  describe('Save Flow - Adversarial Cases', () => {
    it('should surface save errors without crashing', async () => {
      jest.mocked(apiClient.api.ticket).mockResolvedValue(createMockTicket({ title: 'Original' }));
      jest.mocked(apiClient.api.updateTicket).mockRejectedValue(new Error('Save failed'));

      renderButton();
      fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));

      await waitFor(() => {
        expect(screen.getByDisplayValue('Original')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByDisplayValue('Original'), { target: { value: 'Updated title' } });
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

      await waitFor(() => {
        expect(screen.getByText('Save failed')).toBeInTheDocument();
      });
    });
  });
});

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
    child_count: 0,
    revision: 1,
    last_updated_by: 'test@example.com',
    next_agent: 'implementation_agent',
    next_status: 'ready',
    blocking_issues: '',
    state_locked: false,
    workflow_template_slug: 'default',
    workflow_template_name: 'Default',
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

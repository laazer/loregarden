import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import * as apiClient from '../../api/client';
import { TicketRelations } from '../TicketRelations';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

const relatedRef = (over: Partial<apiClient.TicketDependencyRef> = {}): apiClient.TicketDependencyRef => ({
  id: 'ticket-related',
  external_id: 'rel-1',
  title: 'Related work',
  state: 'backlog',
  work_item_type: 'task',
  is_integration_review: false,
  ...over,
});

function mockTicket(over: Partial<apiClient.TicketDetail> = {}): apiClient.TicketDetail {
  return {
    id: 'ticket-1',
    external_id: 'main-1',
    title: 'Main',
    description: '',
    acceptance_criteria: [],
    state: 'in_progress',
    priority: 2,
    workspace_slug: 'loregarden',
    workflow_stage_key: '',
    workflow_stage_status: 'pending',
    workflow_stage_name: '',
    run_code: '',
    work_item_type: 'task',
    parent_ticket_id: null,
    milestone: '',
    branch: '',
    child_count: 0,
    revision: 1,
    last_updated_by: '',
    current_stage_agent: '',
    next_status: '',
    blocking_issues: '',
    state_locked: false,
    workflow_template_slug: '',
    workflow_template_name: '',
    stages: [],
    artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
    ...over,
  };
}

function renderCard(ticket: apiClient.TicketDetail) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TicketRelations ticket={ticket} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

it('lists the related tickets', () => {
  renderCard(mockTicket({ related: [relatedRef()] }));

  expect(screen.getByText('rel-1')).toBeInTheDocument();
  expect(screen.getByText(/Related work/)).toBeInTheDocument();
});

it('says so when nothing is related', () => {
  renderCard(mockTicket({ related: [] }));

  expect(screen.getByText(/nothing related yet/i)).toBeInTheDocument();
});

it('adds a relation by id or external id', async () => {
  jest.mocked(apiClient.api.addRelation).mockResolvedValue(mockTicket());
  renderCard(mockTicket({ related: [] }));

  fireEvent.change(screen.getByPlaceholderText(/relate to ticket/i), {
    target: { value: ' rel-1 ' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));

  await waitFor(() => {
    expect(apiClient.api.addRelation).toHaveBeenCalledWith('ticket-1', 'rel-1');
  });
});

it('removes a relation', async () => {
  jest.mocked(apiClient.api.removeRelation).mockResolvedValue(mockTicket());
  renderCard(mockTicket({ related: [relatedRef()] }));

  fireEvent.click(screen.getByRole('button', { name: /remove related ticket/i }));

  await waitFor(() => {
    expect(apiClient.api.removeRelation).toHaveBeenCalledWith('ticket-1', 'ticket-related');
  });
});

it('surfaces the server error instead of clearing the input', async () => {
  jest.mocked(apiClient.api.addRelation).mockRejectedValue(new Error('Related ticket not found'));
  renderCard(mockTicket({ related: [] }));

  const input = screen.getByPlaceholderText(/relate to ticket/i);
  fireEvent.change(input, { target: { value: 'nope' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));

  await waitFor(() => {
    expect(screen.getByText('Related ticket not found')).toBeInTheDocument();
  });
  expect(input).toHaveValue('nope');
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';

import * as apiClient from '../../api/client';
import { TicketDetailsModal } from '../TicketDetailsModal';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

function mockTicket(over: Partial<apiClient.TicketDetail> = {}): apiClient.TicketDetail {
  return {
    id: 'ticket-1',
    external_id: 'main-1',
    title: 'Main',
    description: '',
    acceptance_criteria: [],
    checked_acceptance_criteria: [],
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

function renderModal(ticket: apiClient.TicketDetail) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TicketDetailsModal ticket={ticket} isOpen onClose={() => {}} />
    </QueryClientProvider>,
  );
}

it('lists what the spec ruled out of scope, each with its reason', () => {
  renderModal(mockTicket({ out_of_scope: ['The no-layout arm — 2230 covers only laid-out regions'] }));

  const list = screen.getByRole('list', { name: /out of scope/i });
  expect(list).toHaveTextContent('The no-layout arm — 2230 covers only laid-out regions');
});

it('shows no out-of-scope section when the spec ruled nothing out', () => {
  renderModal(mockTicket());

  expect(screen.queryByRole('list', { name: /out of scope/i })).not.toBeInTheDocument();
});

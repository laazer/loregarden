import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import * as apiClient from '../../api/client';
import { TicketDetailsModal, type TicketDetailsSaveDraft } from '../TicketDetailsModal';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

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
    next_agent: '',
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

function renderModal(ticket: apiClient.TicketDetail, onSave?: (d: TicketDetailsSaveDraft) => Promise<void>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TicketDetailsModal ticket={ticket} isOpen onClose={() => {}} onSave={onSave} />
    </QueryClientProvider>,
  );
}

const tagsInput = () => screen.getByLabelText(/tags, comma separated/i);

it('seeds the editor from the ticket tags', () => {
  renderModal(mockTicket({ tags: ['backend', 'needs-design'] }));

  expect(tagsInput()).toHaveValue('backend, needs-design');
});

it('saves tags split on commas, trimmed and deduplicated', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  renderModal(mockTicket({ tags: [] }), onSave);

  fireEvent.change(tagsInput(), { target: { value: ' backend , Backend ,, ui ' } });
  fireEvent.click(screen.getByRole('button', { name: /save/i }));

  await waitFor(() => {
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ tags: ['backend', 'ui'] }));
  });
});

it('leaves save disabled when only the tag spelling would round-trip unchanged', () => {
  renderModal(mockTicket({ tags: ['backend'] }), jest.fn());

  fireEvent.change(tagsInput(), { target: { value: 'backend,' } });

  expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
});

it('can clear every tag', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  renderModal(mockTicket({ tags: ['backend'] }), onSave);

  fireEvent.change(tagsInput(), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: /save/i }));

  await waitFor(() => {
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ tags: [] }));
  });
});

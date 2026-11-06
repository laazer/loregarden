/**
 * The PATCH path's five states for the size-cap refusal (lg-milestone-that-2250).
 *
 * LOADING: no new request, so no new loading state — the existing hint is unchanged.
 * EMPTY: "No acceptance criteria yet." unchanged, and the readout reads "0 of 12".
 * ERROR: the refusal renders in the existing saveError slot, edit mode stays open
 *        and the draft is kept verbatim, so no typed criterion is lost.
 * SLOW: Save stays enabled even over the cap — one bad field must not block an
 *       unrelated title edit sharing that button; `isSaving` is the double-click guard.
 * KEYBOARD: the readout is announced and the disabled Add names its reason.
 *
 * The pre-flight signal is the disabled "+ Add criterion"; the refusal is the
 * backstop, which is why Save is never disabled for being over the cap.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import * as apiClient from '../../api/client';
import { MAX_ACCEPTANCE_CRITERIA } from '../../lib/criterionDrafts';
import { TicketDetailsModal, type TicketDetailsSaveDraft } from '../TicketDetailsModal';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

function criteria(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `criterion ${i}`);
}

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

function renderModal(
  ticket: apiClient.TicketDetail,
  options: { onSave?: (d: TicketDetailsSaveDraft) => Promise<void>; saveError?: string } = {},
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const node = (saveError?: string) => (
    <QueryClientProvider client={queryClient}>
      <TicketDetailsModal
        ticket={ticket}
        isOpen
        onClose={() => {}}
        onSave={options.onSave}
        saveError={saveError}
      />
    </QueryClientProvider>
  );
  const view = render(node(options.saveError));
  return { showError: (message: string) => view.rerender(node(message)) };
}

const openEditor = () => fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
const saveButton = () => screen.getByRole('button', { name: /save changes/i });

/** What `describeError` lifts out of the 400's structured `detail.message`. */
const REFUSAL =
  `This ticket would hold ${MAX_ACCEPTANCE_CRITERIA + 1} acceptance criteria; the cap is ` +
  `${MAX_ACCEPTANCE_CRITERIA}. Remove a criterion, or open a new ticket under this one's ` +
  'parent for the extra work. Nothing was saved and your draft is still here.';

it('a cap refusal renders in the save error slot and keeps the draft', async () => {
  const onSave = jest.fn().mockRejectedValue(new Error('refused'));
  const { showError } = renderModal(
    mockTicket({ acceptance_criteria: criteria(MAX_ACCEPTANCE_CRITERIA) }),
    { onSave },
  );
  openEditor();

  const typed = 'the thirteenth criterion, typed by hand and not to be lost';
  fireEvent.change(screen.getByLabelText('Criterion 1'), { target: { value: typed } });
  fireEvent.click(saveButton());
  await waitFor(() => expect(onSave).toHaveBeenCalled());

  showError(REFUSAL);

  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent(String(MAX_ACCEPTANCE_CRITERIA + 1));
  expect(alert).toHaveTextContent(String(MAX_ACCEPTANCE_CRITERIA));
  // Edit mode is still open, and the typed criterion is still in the field.
  expect(saveButton()).toBeInTheDocument();
  expect(screen.getByLabelText('Criterion 1')).toHaveValue(typed);
});

it('Save stays enabled when the draft is over the cap', () => {
  // Title, description, tags, state and priority share this one button. A draft
  // over the criteria cap must not block an edit to any of them.
  renderModal(mockTicket({ acceptance_criteria: criteria(MAX_ACCEPTANCE_CRITERIA + 8) }), {
    onSave: jest.fn(),
  });
  openEditor();

  fireEvent.change(screen.getByLabelText('Ticket title'), { target: { value: 'A better title' } });
  expect(saveButton()).toBeEnabled();
  expect(screen.getByRole('button', { name: /add criterion/i })).toBeDisabled();
});

it('the readout reads zero of the cap on a ticket with no criteria', () => {
  renderModal(mockTicket({ acceptance_criteria: [] }), { onSave: jest.fn() });
  openEditor();

  expect(screen.getByText(new RegExp(`0 of ${MAX_ACCEPTANCE_CRITERIA}`))).toBeInTheDocument();
  expect(screen.getByText(/no acceptance criteria yet/i)).toBeInTheDocument();
});

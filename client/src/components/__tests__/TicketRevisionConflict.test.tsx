/** What the operator sees when something else wrote to the ticket they were editing.
 *
 * The question this surface answers: "did my edit land, or did something else
 * overwrite it while I was typing?", asked at the moment Save is clicked.
 *
 * The thing that makes it usable is the filter. Measured on the live database
 * read-only on 2026-10-20: 1,722 tickets, mean `revision` 826, max 82,334, and
 * 1,257-4,333 bumps per agent run. `revision` is a log-line counter, not an
 * edit counter, so a pane that showed every mismatch would interrupt nearly
 * every save made while a run was live, almost always about a field the
 * operator had not touched. So the pane sends the token, and on a 409 refetches
 * and compares only the fields in the patch it submitted: if none moved it
 * resubmits once and says nothing, and only a real collision becomes a state
 * the operator has to answer.
 *
 * These tests cover, in order: which snapshot the token and the diff come from,
 * the filtered 409, the real one, the bound on resubmits, the refetch that
 * itself fails, the two buttons, both Escapes, and the read-mode criterion tick
 * that deliberately sends no token at all.
 */

import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardTicketDetailsButton } from '../DashboardTicketDetailsButton';
import * as apiClient from '../../api/client';
import { ApiError } from '../../api/http';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

const TICKET_ID = 'ticket-123';

function createMockTicket(overrides?: Partial<apiClient.TicketDetail>): apiClient.TicketDetail {
  return {
    id: TICKET_ID,
    external_id: 'lg-workflow-integrity-877',
    title: 'Original title',
    description: 'Original description',
    acceptance_criteria: ['One'],
    checked_acceptance_criteria: [],
    state: 'in_progress',
    priority: 1,
    workspace_slug: 'loregarden',
    workflow_stage_key: 'test_design',
    workflow_stage_status: 'running',
    workflow_stage_name: 'Test Design',
    run_code: 'run_abc123',
    work_item_type: 'bug',
    parent_ticket_id: null,
    milestone: '',
    branch: 'main',
    child_count: 0,
    revision: 5,
    last_updated_by: 'human',
    current_stage_agent: 'test_designer',
    next_status: 'ready',
    blocking_issues: '',
    state_locked: false,
    workflow_template_slug: 'default',
    workflow_template_name: 'Default',
    stages: [],
    artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
    ...overrides,
  };
}

/** A 409 as it reaches the pane: the status plus the parsed body the wrapper lifted. */
function revisionConflict(expected: number, current: number): ApiError {
  const detail = {
    error: 'revision_conflict',
    expected_revision: expected,
    current_revision: current,
    message: `This ticket moved on: you edited revision ${expected} and it is now ${current}.`,
  };
  const error = new ApiError(409, JSON.stringify(detail));
  (error as unknown as { payload: unknown }).payload = { detail };
  return error;
}

describe('the ticket pane under a revision conflict', () => {
  let queryClient: QueryClient;
  /** What `api.ticket` serves right now; reassign to move the ticket under the editor. */
  let served: apiClient.TicketDetail;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    jest.clearAllMocks();
    served = createMockTicket();
    jest.mocked(apiClient.api.ticket).mockImplementation(async () => served);
    jest.mocked(apiClient.api.updateTicket).mockImplementation(async () => served);
  });

  afterEach(() => {
    queryClient.clear();
  });

  const renderButton = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <DashboardTicketDetailsButton ticketId={TICKET_ID} />
      </QueryClientProvider>,
    );

  const openModal = async () => {
    fireEvent.click(screen.getByRole('button', { name: /view ticket details/i }));
    await screen.findByRole('dialog');
  };

  const startEditing = async () => {
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    return screen.findByLabelText('Ticket title');
  };

  const save = () => fireEvent.click(screen.getByRole('button', { name: /^Save changes$|^Saving/ }));

  /** Force the ticket query to refetch what `served` now holds, editor open. */
  const refetchUnderTheEditor = async () => {
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['ticket', TICKET_ID] });
    });
  };

  const patchOf = (call: number) =>
    jest.mocked(apiClient.api.updateTicket).mock.calls[call][1] as Record<string, unknown>;

  const conflictBlock = () => screen.getByRole('alert');

  // ---- which snapshot the save is based on ---------------------------

  it('sends the revision the draft was seeded from, not the one the query refetched', async () => {
    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });

    // Something else edits the description and bumps the revision while the
    // editor is open. The query refetches — it does so on every invalidation
    // from anywhere in this pane — and the draft must not follow it.
    served = createMockTicket({ revision: 9, description: 'Rewritten by a stage' });
    await refetchUnderTheEditor();
    expect(screen.getByLabelText('Ticket title')).toHaveValue('My new title');

    save();

    await waitFor(() => expect(apiClient.api.updateTicket).toHaveBeenCalledTimes(1));
    const patch = patchOf(0);
    // The token names the row the operator's text was based on. Taking it from
    // the live query data instead would claim a revision they never read.
    expect(patch.expected_revision).toBe(5);
    // And the patch carries only what the operator changed. Diffed against the
    // live data it would also carry `description: 'Original description'`,
    // silently reverting the other writer — which is the bug this pane is for.
    expect(patch).toEqual({ title: 'My new title', expected_revision: 5 });
  });

  // ---- the filtered 409 ----------------------------------------------

  it('resubmits once and says nothing when the 409 touched none of its fields', async () => {
    jest
      .mocked(apiClient.api.updateTicket)
      .mockRejectedValueOnce(revisionConflict(5, 7))
      .mockImplementationOnce(async () => served);

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    // The revision moved; the description moved with it. Neither is in the patch.
    served = createMockTicket({ revision: 7, description: 'A stage wrote this' });

    save();

    await waitFor(() => expect(apiClient.api.updateTicket).toHaveBeenCalledTimes(2));
    // Same patch, against the revision just fetched.
    expect(patchOf(1)).toEqual({ title: 'My new title', expected_revision: 7 });
    // Nothing to resolve, so nothing is shown and the save simply lands.
    expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText('Ticket title')).not.toBeInTheDocument());
  });

  // ---- the real one --------------------------------------------------

  it('names the field that moved, keeps the draft, and does not resubmit', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    // The other writer touched the same field the operator did.
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();

    const block = await waitFor(() => conflictBlock());
    expect(block).toHaveTextContent('Title changed while you were editing.');
    expect(apiClient.api.updateTicket).toHaveBeenCalledTimes(1);

    // The operator's text is still in the input. Nothing clears it but their
    // own click on "Discard my text and load latest".
    expect(screen.getByLabelText('Ticket title')).toHaveValue('My new title');

    // A revision of 826 on average, and 82,334 at worst, tells a person nothing.
    expect(block.textContent).not.toMatch(/\d/);
    // Amber, not red: this is a question, not a failure.
    expect(screen.queryByText(/failed to save/i)).not.toBeInTheDocument();
  });

  it('lists several moved fields as a readable sentence', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    fireEvent.change(screen.getByLabelText('Description (markdown)'), {
      target: { value: 'My new description' },
    });
    served = createMockTicket({
      revision: 7,
      title: 'Renamed by someone else',
      description: 'And rewritten too',
    });

    save();

    const block = await waitFor(() => conflictBlock());
    expect(block).toHaveTextContent('Title and Description changed while you were editing.');
  });

  it('takes focus when it appears, and offers exactly two named buttons', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();

    const block = await waitFor(() => conflictBlock());
    expect(block).toHaveFocus();
    const keep = screen.getByRole('button', { name: 'Keep my text' });
    const discard = screen.getByRole('button', { name: 'Discard my text and load latest' });
    expect(block).toContainElement(keep);
    expect(block).toContainElement(discard);
  });

  it('resubmits the identical patch against the fetched revision on "Keep my text"', async () => {
    jest
      .mocked(apiClient.api.updateTicket)
      .mockRejectedValueOnce(revisionConflict(5, 7))
      .mockImplementationOnce(async () => served);

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();
    fireEvent.click(await screen.findByRole('button', { name: 'Keep my text' }));

    await waitFor(() => expect(apiClient.api.updateTicket).toHaveBeenCalledTimes(2));
    expect(patchOf(1)).toEqual({ title: 'My new title', expected_revision: 7 });
  });

  it('reseeds the draft from the server on "Discard my text and load latest"', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();
    fireEvent.click(await screen.findByRole('button', { name: 'Discard my text and load latest' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Ticket title')).toHaveValue('Renamed by someone else'),
    );
    expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument();
  });

  it('disables both buttons while a resubmit is in flight', async () => {
    let landTheResubmit: (() => void) | undefined;
    jest
      .mocked(apiClient.api.updateTicket)
      .mockRejectedValueOnce(revisionConflict(5, 7))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            landTheResubmit = () => resolve(served);
          }),
      );

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();
    const keep = await screen.findByRole('button', { name: 'Keep my text' });
    fireEvent.click(keep);

    await waitFor(() => expect(keep).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Discard my text and load latest' })).toBeDisabled();
    // A second click cannot fan the one save out into two writes.
    fireEvent.click(keep);
    expect(apiClient.api.updateTicket).toHaveBeenCalledTimes(2);

    await act(async () => {
      landTheResubmit?.();
    });
  });

  // ---- the bound on automatic resubmits ------------------------------

  it('shows the conflict after a second 409, rather than retrying forever', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    // Nothing in the patch moved, so the pane resubmits — and is refused again.
    served = createMockTicket({ revision: 7, description: 'A stage wrote this' });

    save();

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(apiClient.api.updateTicket).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Keep my text' })).toBeInTheDocument();
  });

  // ---- error, which is not conflict ----------------------------------

  it('reports a failed follow-up refetch as an error, not as a conflict', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    // The check itself cannot be made.
    jest.mocked(apiClient.api.ticket).mockRejectedValue(new Error('Network down'));

    save();

    const alert = await waitFor(() => screen.getByRole('alert'));
    // The existing saveError path, so the operator is told the check failed and
    // their text is still there — and never the amber conflict block, which
    // would claim a comparison nobody made.
    expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument();
    expect(alert).toHaveTextContent(/could not|failed/i);
    expect(screen.getByLabelText('Ticket title')).toHaveValue('My new title');
  });

  // ---- keyboard ------------------------------------------------------

  it('dismisses the block on the first Escape and keeps the editor and the draft', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();
    await screen.findByRole('button', { name: 'Keep my text' });

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument(),
    );
    // Today's handler leaves edit mode and nulls the draft, which would discard
    // the very text this state exists to protect.
    expect(screen.getByLabelText('Ticket title')).toHaveValue('My new title');
  });

  it('leaves edit mode on the second Escape, as it always has', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();
    await screen.findByRole('button', { name: 'Keep my text' });

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument(),
    );
    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByLabelText('Ticket title')).not.toBeInTheDocument());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  // ---- the tick that sends no token ----------------------------------

  it('sends no token when a criterion is ticked from read mode', async () => {
    jest.mocked(apiClient.api.updateTicket).mockImplementation(async () => served);

    renderButton();
    await openModal();

    fireEvent.click(await screen.findByRole('checkbox', { name: 'One' }));

    await waitFor(() => expect(apiClient.api.updateTicket).toHaveBeenCalled());
    const patch = patchOf(0);
    // One field, no unsaved text to protect, and the server's reply is already
    // authoritative. A token here would conflict against the bump firehose with
    // nothing for the operator to resolve.
    expect(patch).not.toHaveProperty('expected_revision');
    expect(patch).toEqual({ checked_acceptance_criteria: ['One'] });
  });
});

/** The pane's 409 filter, on the fields and failures its own tests do not reach.
 *
 * `TicketRevisionConflict.test.tsx` covers the two headline paths: a mismatch
 * that touched nothing the operator edited (resubmit once, say nothing) and one
 * that touched the title (show the block). Both move a plain string field, and
 * both arrive as a revision conflict.
 *
 * The filter has to decide "did this field move?" for seven fields, three of
 * which are arrays and one of which is routinely the empty string, and it has
 * to decide "is this even a revision conflict?" for every other failure the
 * save can produce. Those are the decisions here. Each test names the
 * implementation it falsifies, because every one of these is a filter that
 * passes the suite above:
 *
 *   - comparing arrays with `!==`                → always a conflict, on every save
 *   - skipping falsy patch values                → a cleared field never conflicts
 *   - naming every key in the patch              → a notice about fields nobody touched
 *   - treating any 409 as a revision conflict    → the amber block for an unrelated refusal
 *   - treating any failure as a conflict         → a refetch and a resubmit after a 500
 *
 * The measurement behind all of it: `revision` runs at a mean of 826 and
 * 1,257-4,333 bumps per agent run (live database, read-only, 2026-10-20). A
 * filter that is wrong in the direction of "conflict" interrupts nearly every
 * save; one that is wrong in the direction of "resubmit" is the lost update
 * this ticket exists to stop.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
    workflow_stage_key: 'test_break',
    workflow_stage_status: 'running',
    workflow_stage_name: 'Break Tests',
    run_code: 'run_abc123',
    work_item_type: 'bug',
    parent_ticket_id: null,
    milestone: '',
    branch: 'main',
    child_count: 0,
    revision: 5,
    last_updated_by: 'human',
    current_stage_agent: 'test_breaker',
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

/** A revision conflict as it reaches the pane: the status plus the parsed body. */
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

/** Some other 409 — a string detail, like every other refusal in the API. */
function unrelatedConflict(): ApiError {
  const error = new ApiError(409, 'That branch is already attached to another ticket');
  (error as unknown as { payload: unknown }).payload = {
    detail: 'That branch is already attached to another ticket',
  };
  return error;
}

describe('the pane deciding whether a 409 is the operator problem', () => {
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

  const patchOf = (call: number) =>
    jest.mocked(apiClient.api.updateTicket).mock.calls[call][1] as Record<string, unknown>;

  const calls = () => jest.mocked(apiClient.api.updateTicket).mock.calls.length;

  // ---- the array fields ----------------------------------------------

  it('does not call an array a conflict for having been rebuilt', async () => {
    // The operator edits a criterion. Something else bumps the revision and
    // leaves the criteria alone — but every fetch deserialises JSON, so the
    // fresh array is a different object with the same contents. Compared with
    // `!==` that is a conflict on every save a run is live for, which at
    // 1,257-4,333 bumps per run is nearly all of them.
    jest
      .mocked(apiClient.api.updateTicket)
      .mockRejectedValueOnce(revisionConflict(5, 7))
      .mockImplementationOnce(async () => served);

    renderButton();
    await openModal();
    await startEditing();
    fireEvent.change(screen.getByLabelText('Criterion 1'), { target: { value: 'One, edited' } });
    served = createMockTicket({ revision: 7, acceptance_criteria: ['One'] });

    save();

    await waitFor(() => expect(calls()).toBe(2));
    expect(patchOf(1)).toEqual({ acceptance_criteria: ['One, edited'], expected_revision: 7 });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('names the criteria by their label when they really did move', async () => {
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    await startEditing();
    fireEvent.change(screen.getByLabelText('Criterion 1'), { target: { value: 'One, edited' } });
    served = createMockTicket({ revision: 7, acceptance_criteria: ['One', 'Two, by a stage'] });

    save();

    const block = await waitFor(() => screen.getByRole('alert'));
    expect(block).toHaveTextContent('Acceptance criteria changed while you were editing.');
    expect(calls()).toBe(1);
    expect(screen.getByLabelText('Criterion 1')).toHaveValue('One, edited');
  });

  // ---- a field the operator cleared ----------------------------------

  it('compares a field the operator emptied, rather than skipping it as falsy', async () => {
    // `patch.description` is '' here. A filter that iterates the patch and skips
    // values it finds falsy would compare nothing, find nothing moved, and
    // resubmit the blanking over the top of whatever the other writer wrote —
    // the worst available outcome, because the field is empty afterwards and
    // the mean description is 2,434 characters.
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    await startEditing();
    fireEvent.change(screen.getByLabelText('Description (markdown)'), { target: { value: '' } });
    served = createMockTicket({ revision: 7, description: 'Rewritten by a stage' });

    save();

    const block = await waitFor(() => screen.getByRole('alert'));
    expect(block).toHaveTextContent('Description changed while you were editing.');
    expect(calls()).toBe(1);
    expect(screen.getByLabelText('Description (markdown)')).toHaveValue('');
  });

  // ---- which fields the block is allowed to name ---------------------

  it('names only the field that moved, of the several the patch carried', async () => {
    // Two fields patched, one moved, and a third moved that the operator never
    // touched. A block built from the patch's keys names both of theirs; one
    // built from the fields that moved in the whole ticket names the priority.
    // Neither is what C8 asks for, and both are read by the operator as "your
    // title is at risk" when it is not.
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    fireEvent.change(screen.getByLabelText('Description (markdown)'), {
      target: { value: 'My new description' },
    });
    served = createMockTicket({ revision: 7, description: 'Rewritten by a stage', priority: 3 });

    save();

    const block = await waitFor(() => screen.getByRole('alert'));
    expect(block).toHaveTextContent('Description changed while you were editing.');
    expect(block.textContent).not.toMatch(/Title/);
    expect(block.textContent).not.toMatch(/Priority/);
  });

  it('names a non-text field by its label as well', async () => {
    // The field map in C8 has seven entries and the two headline tests exercise
    // two of them. A map keyed only for the text fields falls through to the
    // raw key — "state changed while you were editing" — on the fields an
    // operator is most likely to be racing a stage on.
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    await startEditing();
    fireEvent.change(screen.getByLabelText('State'), { target: { value: 'blocked' } });
    served = createMockTicket({ revision: 7, state: 'done' });

    save();

    const block = await waitFor(() => screen.getByRole('alert'));
    expect(block).toHaveTextContent('State changed while you were editing.');
    expect(block.textContent).not.toMatch(/\d/);
  });

  // ---- failures that are not this ------------------------------------

  it('leaves an unrelated 409 on the error path', async () => {
    // `errorStatus(err) === 409` is not the test. A refusal that happens to
    // share the status carries no revision, so the pane has nothing to compare
    // and nothing to resubmit — and the amber block would offer the operator
    // two buttons for a problem neither of them addresses.
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(unrelatedConflict());

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });

    save();

    const alert = await waitFor(() => screen.getByRole('alert'));
    expect(alert).toHaveTextContent(/already attached/i);
    expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument();
    expect(calls()).toBe(1);
    expect(screen.getByLabelText('Ticket title')).toHaveValue('My new title');
  });

  it('does not refetch or resubmit after a failure that is not a conflict', async () => {
    jest
      .mocked(apiClient.api.updateTicket)
      .mockRejectedValue(new ApiError(500, 'Internal Server Error'));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    const fetchesBeforeSave = jest.mocked(apiClient.api.ticket).mock.calls.length;

    save();

    const alert = await waitFor(() => screen.getByRole('alert'));
    expect(alert).toHaveTextContent(/Internal Server Error|Failed to save/i);
    expect(calls()).toBe(1);
    expect(jest.mocked(apiClient.api.ticket).mock.calls.length).toBe(fetchesBeforeSave);
    expect(screen.queryByRole('button', { name: 'Keep my text' })).not.toBeInTheDocument();
  });

  // ---- the bound on the operator's own retry -------------------------

  it('keeps the block, and adds no third write, when "Keep my text" is refused again', async () => {
    // The automatic resubmit is bounded at one per save. The button is the
    // operator's, and it has to be bounded per click for the same reason: the
    // ticket is being written to several times a second by a live run, so
    // "retry until it lands" is a loop with no exit.
    jest.mocked(apiClient.api.updateTicket).mockRejectedValue(revisionConflict(5, 7));

    renderButton();
    await openModal();
    const title = await startEditing();
    fireEvent.change(title, { target: { value: 'My new title' } });
    served = createMockTicket({ revision: 7, title: 'Renamed by someone else' });

    save();
    const keep = await screen.findByRole('button', { name: 'Keep my text' });
    served = createMockTicket({ revision: 9, title: 'Renamed again by someone else' });
    fireEvent.click(keep);

    await waitFor(() => expect(calls()).toBe(2));
    // Still the operator's problem to resolve, and still their text.
    expect(await screen.findByRole('button', { name: 'Keep my text' })).toBeInTheDocument();
    expect(screen.getByLabelText('Ticket title')).toHaveValue('My new title');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Keep my text' })).not.toBeDisabled(),
    );
    expect(calls()).toBe(2);
  });
});

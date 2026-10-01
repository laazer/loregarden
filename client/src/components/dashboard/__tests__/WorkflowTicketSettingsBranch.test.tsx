import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { WorkflowTicketSettings } from '../WorkflowTicketSettings';
import { useTicketBranchSave } from '../../../hooks/useTicketBranchSave';
import * as apiClient from '../../../api/client';
import { useToastStore } from '../../../state/toastStore';

jest.mock('../../../api/client', () => jest.requireActual('../../../test/apiClientMock'));

/**
 * The branch field saves what the operator typed.
 *
 * It used to write each keystroke into the cached ticket detail and then, on blur, compare the
 * input against that same cache — always equal, so the save never fired. The run path compared
 * against the same cache and skipped its save too, starting the run on the server's old branch.
 */

const TICKET_ID = 'ticket-branch';
const api = apiClient.api as jest.Mocked<typeof apiClient.api>;

let serverBranch = '';

const mkTicket = (): apiClient.TicketDetail => ({
  id: TICKET_ID,
  external_id: 'lg-branch',
  title: 'Branch field',
  description: '',
  acceptance_criteria: [],
  state: 'in_progress',
  priority: 1,
  workspace_slug: 'loregarden',
  workflow_stage_key: 'implement',
  workflow_stage_status: 'pending',
  workflow_stage_name: 'Implement',
  run_code: '',
  work_item_type: 'task',
  parent_ticket_id: null,
  milestone: '',
  branch: serverBranch,
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
});

/** Wired the way the Console wires it: the ticket is the cached detail query. */
function Harness() {
  const detail = useQuery({ queryKey: ['ticket', TICKET_ID], queryFn: () => api.ticket(TICKET_ID) });
  const { save } = useTicketBranchSave();
  if (!detail.data) return null;
  return (
    <WorkflowTicketSettings
      ticket={detail.data}
      workflowTemplates={[]}
      workspaceWorkflow={undefined}
      workflowBusy={false}
      templatePending={false}
      postureSaving={false}
      onWorkflowChange={jest.fn()}
      onSaveBranch={(branch) => save(TICKET_ID, branch)}
      onPostureChange={jest.fn()}
    />
  );
}

function renderField() {
  // No background refetch: a refetch between typing and blur is what used to hide the bug.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
  return queryClient;
}

const branchInput = () => screen.findByPlaceholderText('loregarden/lg-branch');

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  serverBranch = 'loregarden/lg-branch';
  api.ticket.mockImplementation(async () => mkTicket());
  api.updateTicket.mockImplementation(async (_id, body) => {
    serverBranch = body.branch ?? serverBranch;
    return mkTicket();
  });
});

describe('WorkflowTicketSettings branch field', () => {
  it('saves the typed branch on blur', async () => {
    renderField();
    const input = await branchInput();

    fireEvent.change(input, { target: { value: '  feature/new  ' } });
    fireEvent.blur(input);

    await waitFor(() => expect(api.updateTicket).toHaveBeenCalledWith(TICKET_ID, { branch: 'feature/new' }));
    await waitFor(() => expect(input).toHaveValue('feature/new'));
  });

  it('does not save when the branch is unchanged', async () => {
    renderField();
    const input = await branchInput();

    fireEvent.change(input, { target: { value: 'loregarden/lg-branch ' } });
    fireEvent.blur(input);

    await waitFor(() => expect(input).toHaveValue('loregarden/lg-branch'));
    expect(api.updateTicket).not.toHaveBeenCalled();
  });

  it('saves main from the Use main button', async () => {
    renderField();
    const input = await branchInput();

    fireEvent.click(screen.getByRole('button', { name: 'Use main' }));

    await waitFor(() => expect(api.updateTicket).toHaveBeenCalledWith(TICKET_ID, { branch: 'main' }));
    await waitFor(() => expect(input).toHaveValue('main'));
  });

  it('keeps the cached ticket at the server value while typing, so the run path compares honestly', async () => {
    const queryClient = renderField();
    const input = await branchInput();

    fireEvent.change(input, { target: { value: 'feature/unsaved' } });

    expect(input).toHaveValue('feature/unsaved');
    expect(queryClient.getQueryData<apiClient.TicketDetail>(['ticket', TICKET_ID])?.branch).toBe('loregarden/lg-branch');
  });

  it('reverts to the stored branch and says so when the save fails', async () => {
    api.updateTicket.mockRejectedValue(new Error('disk full'));
    renderField();
    const input = await branchInput();

    fireEvent.change(input, { target: { value: 'feature/rejected' } });
    fireEvent.blur(input);

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
    expect(useToastStore.getState().toasts[0]).toMatchObject({ tone: 'error' });
    await waitFor(() => expect(input).toHaveValue('loregarden/lg-branch'));
  });
});

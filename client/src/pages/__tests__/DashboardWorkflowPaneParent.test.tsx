import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import * as apiClient from '../../api/client';
import { RouterBridgeSync } from '../../components/RouterBridgeSync';
import { useUiStore } from '../../state/uiStore';
import { Dashboard } from '../Dashboard';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

const PARENT_ID = 'ticket-parent';
const CHILD_ID = 'ticket-child';

const mkWorkspace = (): apiClient.WorkspaceSummary => ({
  id: 'ws-1',
  slug: 'loregarden',
  name: 'Loregarden',
  repo_path: '.',
  repo_root: '/repo',
  repo_exists: true,
  ticket_count: 0,
  blocked_count: 0,
  workflow_template_slug: '',
  cli_adapter: '',
  claude_model: '',
  cursor_model: '',
  lmstudio_base_url: '',
  lmstudio_model: '',
});

function mockTicket(over: Partial<apiClient.TicketDetail> = {}): apiClient.TicketDetail {
  return {
    id: CHILD_ID,
    external_id: 'child-1',
    title: 'Child Ticket',
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

const tree: apiClient.TicketTreeNode[] = [
  {
    id: PARENT_ID,
    external_id: 'parent-1',
    title: 'Parent Ticket',
    state: 'in_progress',
    priority: 2,
    work_item_type: 'feature',
    workflow_stage_name: '',
    workflow_stage_status: 'pending',
    child_count: 1,
    children: [
      {
        id: CHILD_ID,
        external_id: 'child-1',
        title: 'Child Ticket',
        state: 'in_progress',
        priority: 2,
        work_item_type: 'task',
        workflow_stage_name: '',
        workflow_stage_status: 'pending',
        child_count: 0,
        children: [],
      },
    ],
  },
];

function renderDashboard(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <RouterBridgeSync />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/tickets/:ticketId" element={<Dashboard />} />
          <Route path="/tickets/:ticketId/:artifactTab" element={<Dashboard />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUiStore.setState({
    stateFilters: [],
    typeFilters: [],
    search: '',
    expandedTicketIds: [],
    workspace: 'all',
    paneVisibility: { workspaces: true, tickets: true, workflow: true, artifacts: true },
  });
  useUiStore.persist?.clearStorage?.();
  jest.clearAllMocks();
  jest.mocked(apiClient.api.workspaces).mockResolvedValue([mkWorkspace()]);
  jest.mocked(apiClient.api.ticketTree).mockResolvedValue(tree);
  jest.mocked(apiClient.api.runs).mockResolvedValue([]);
  jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
  jest.mocked(apiClient.api.ticket).mockImplementation(async (id: string) =>
    id === PARENT_ID
      ? mockTicket({ id: PARENT_ID, external_id: 'parent-1', title: 'Parent Ticket', work_item_type: 'feature' })
      : mockTicket({ parent_ticket_id: PARENT_ID }),
  );
});

const parentButton = () => screen.getByRole('button', { name: /parent/i });

it('offers a parent jump when the selected ticket has one', async () => {
  renderDashboard(`/tickets/${CHILD_ID}`);

  await waitFor(() => expect(parentButton()).toBeInTheDocument());
  expect(screen.getByText('Workflow').closest('.workflow-pane')).toContainElement(parentButton());
});

it('selects the parent when the button is clicked', async () => {
  renderDashboard(`/tickets/${CHILD_ID}`);

  await waitFor(() => expect(parentButton()).toBeInTheDocument());
  fireEvent.click(parentButton());

  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Parent Ticket', level: 1 })).toBeInTheDocument();
  });
});

it('offers no parent jump on a top-level ticket', async () => {
  renderDashboard(`/tickets/${PARENT_ID}`);

  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Parent Ticket', level: 1 })).toBeInTheDocument();
  });
  expect(screen.queryByRole('button', { name: /^↑ Parent$/ })).not.toBeInTheDocument();
});

it('shows the ticket tags as pills in the workflow pane', async () => {
  jest.mocked(apiClient.api.ticket).mockResolvedValue(
    mockTicket({ parent_ticket_id: PARENT_ID, tags: ['backend', 'needs-design'] }),
  );

  renderDashboard(`/tickets/${CHILD_ID}`);

  await waitFor(() => expect(screen.getByText('backend')).toBeInTheDocument());
  expect(screen.getByText('needs-design')).toBeInTheDocument();
});

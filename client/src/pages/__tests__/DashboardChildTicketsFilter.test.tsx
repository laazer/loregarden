import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Dashboard } from '../Dashboard';
import { RouterBridgeSync } from '../../components/RouterBridgeSync';
import * as apiClient from '../../api/client';
import { useUiStore } from '../../state/uiStore';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

/**
 * The "Child tickets" list describes the selected ticket, so a sidebar filter must not shrink it.
 *
 * The list used to read out of the sidebar's filtered tree, while its `child_count` gate came from
 * the unfiltered ticket detail. A milestone with 5 children and 1 in-progress child therefore
 * announced 5 children and rendered 1.
 */

const MILESTONE_ID = 'milestone-bug-hole';

const mkWorkspace = (): apiClient.WorkspaceSummary => ({
  id: 'ws-1',
  slug: 'loregarden',
  name: 'Loregarden',
  repo_path: '.',
  repo_root: '/repo',
  repo_exists: true,
  ticket_count: 6,
  blocked_count: 0,
  workflow_template_slug: '',
  cli_adapter: '',
  claude_model: '',
  cursor_model: '',
  lmstudio_base_url: '',
  lmstudio_model: '',
});

const mkNode = (over: Partial<apiClient.TicketTreeNode>): apiClient.TicketTreeNode => ({
  id: 'node',
  external_id: 'node',
  title: 'Node',
  state: 'backlog',
  priority: 3,
  work_item_type: 'bug',
  workspace_slug: 'loregarden',
  workflow_stage_name: 'Triage',
  workflow_stage_status: 'pending',
  child_count: 0,
  children: [],
  ...over,
});

const CHILDREN = [
  mkNode({ id: 'c-81', external_id: '81-continue-and-unblock', title: 'Continue and Unblock' }),
  mkNode({
    id: 'c-82',
    external_id: '82-show-child-tickets',
    title: 'Show child tickets regardless of sidebar filter state',
    state: 'in_progress',
  }),
  mkNode({ id: 'c-83', external_id: '83-questions-and-answers', title: 'Questions and Answers in Baxter chat' }),
  mkNode({ id: 'c-84', external_id: '84-append-learning-broken', title: 'loregarden_append_learning broken' }),
  mkNode({ id: 'c-85', external_id: '85-missing-title-line', title: 'Markdown ticket is missing a Title line' }),
];

const milestoneWith = (children: apiClient.TicketTreeNode[]) =>
  mkNode({
    id: MILESTONE_ID,
    external_id: '80-bug-hole',
    title: 'Bug Hole',
    work_item_type: 'milestone',
    state: 'in_progress',
    child_count: children.length,
    children,
  });

// Mirrors the server: an unfiltered call returns every child; state=in_progress prunes the tree
// down to the one matching child, with the milestone retained only as its ancestor.
const FULL_TREE = [milestoneWith(CHILDREN)];
const FILTERED_TREE = [milestoneWith([CHILDREN[1]])];

const mkDetail = (): apiClient.TicketDetail => ({
  id: MILESTONE_ID,
  external_id: '80-bug-hole',
  title: 'Bug Hole',
  description: '',
  acceptance_criteria: [],
  state: 'in_progress',
  priority: 2,
  workspace_slug: 'loregarden',
  workflow_stage_key: 'triage',
  workflow_stage_status: 'pending',
  workflow_stage_name: 'Triage',
  run_code: '',
  work_item_type: 'milestone',
  parent_ticket_id: null,
  milestone: '',
  branch: '',
  workflow_template_slug: 'default',
  workflow_template_name: 'Default',
  // A true DB count, unaffected by the sidebar filter — this is what gates the panel.
  child_count: CHILDREN.length,
  revision: 1,
  last_updated_by: 'test',
  next_agent: '',
  next_status: '',
  blocking_issues: '',
  state_locked: false,
  stages: [],
  artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
});

describe('Dashboard — child tickets ignore the sidebar filter', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    useUiStore.setState({
      stateFilters: [],
      typeFilters: [],
      search: '',
      expandedTicketIds: [],
      workspace: 'loregarden',
      paneVisibility: { workspaces: true, tickets: true, workflow: true, artifacts: true },
    });
    useUiStore.persist?.clearStorage?.();
    jest.clearAllMocks();

    jest.mocked(apiClient.api.workspaces).mockResolvedValue([mkWorkspace()]);
    jest.mocked(apiClient.api.ticket).mockResolvedValue(mkDetail());
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
    jest.mocked(apiClient.api.workflowTemplates).mockResolvedValue([]);

    jest
      .mocked(apiClient.api.ticketTree)
      .mockImplementation(async (params?: Parameters<typeof apiClient.api.ticketTree>[0]) =>
        params?.state?.length || params?.work_item_type?.length ? FILTERED_TREE : FULL_TREE,
      );
  });

  const renderDashboard = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/tickets/${MILESTONE_ID}`]}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tickets/:ticketId" element={<Dashboard />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  const childPanel = async () => {
    const heading = await screen.findByText('Child tickets');
    return heading.parentElement as HTMLElement;
  };

  it('lists every child of the selected milestone while the sidebar filters to in_progress', async () => {
    useUiStore.setState({ stateFilters: ['in_progress'] });
    renderDashboard();

    const panel = await childPanel();
    await waitFor(() => {
      expect(within(panel).getByText(/Continue and Unblock/)).toBeInTheDocument();
    });

    for (const child of CHILDREN) {
      expect(within(panel).getByText(new RegExp(child.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeInTheDocument();
    }
  });

  it('lists every child while the sidebar filters by work item type', async () => {
    useUiStore.setState({ typeFilters: ['bug'] });
    renderDashboard();

    const panel = await childPanel();
    await waitFor(() => {
      expect(within(panel).getByText(/Questions and Answers in Baxter chat/)).toBeInTheDocument();
    });
    expect(within(panel).getByText(/Markdown ticket is missing a Title line/)).toBeInTheDocument();
  });

  it('still lists every child when no filter is active', async () => {
    renderDashboard();

    const panel = await childPanel();
    await waitFor(() => {
      expect(within(panel).getByText(/Continue and Unblock/)).toBeInTheDocument();
    });
    expect(within(panel).getByText(/Markdown ticket is missing a Title line/)).toBeInTheDocument();
  });

  it('does not fetch a second tree when no filter is narrowing the sidebar', async () => {
    renderDashboard();
    await childPanel();

    await waitFor(() => expect(apiClient.api.ticketTree).toHaveBeenCalled());
    const calls = jest.mocked(apiClient.api.ticketTree).mock.calls;
    expect(calls.every(([params]) => !params?.state?.length && !params?.work_item_type?.length)).toBe(true);
  });
});

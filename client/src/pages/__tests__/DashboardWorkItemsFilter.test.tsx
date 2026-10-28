import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Dashboard } from '../Dashboard';
import { RouterBridgeSync } from '../../components/RouterBridgeSync';
import * as apiClient from '../../api/client';
import { useUiStore } from '../../state/uiStore';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

/** The Console's Work items pane narrows its own tree by title or id. */

const MILESTONE_ID = 'milestone-bug-hole';

const mkWorkspace = (): apiClient.WorkspaceSummary => ({
  id: 'ws-1',
  slug: 'loregarden',
  name: 'Loregarden',
  repo_path: '.',
  repo_root: '/repo',
  repo_exists: true,
  repo_state: "repository",
  ticket_count: 6,
  blocked_count: 0,
  workflow_template_slug: '',
  archived_at: null,
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
  checked_acceptance_criteria: [],
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
  current_stage_agent: '',
  next_status: '',
  blocking_issues: '',
  state_locked: false,
  stages: [],
  artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
});

describe('Dashboard — the Work items pane filters its own tree', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    useUiStore.setState({
      stateFilters: [],
      typeFilters: [],
      search: '',
      expandedTicketIds: [],
      workspace: 'loregarden',
      paneVisibility: { workspaces: true, tickets: true },
    });
    jest.clearAllMocks();
    jest.mocked(apiClient.api.workspaces).mockResolvedValue([mkWorkspace()]);
    jest.mocked(apiClient.api.ticket).mockResolvedValue(mkDetail());
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
    jest.mocked(apiClient.api.workflowTemplates).mockResolvedValue([]);
    // The server keeps a match's ancestors; nothing matches "zzz".
    jest
      .mocked(apiClient.api.ticketTree)
      .mockImplementation(async (params?: Parameters<typeof apiClient.api.ticketTree>[0]) =>
        params?.search === 'zzz' ? [] : params?.search ? FILTERED_TREE : FULL_TREE,
      );
  });

  const renderAt = (path: string) =>
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/console" element={<Dashboard />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it('sits in the pane beside the tree, narrows it by title or id, and Escape clears it', async () => {
    const { container } = renderAt('/console');
    const field = await screen.findByRole('searchbox', { name: 'Filter work items by title or id' });
    // In the pane it narrows, not in the app's topbar.
    expect(container.querySelector('.tickets-pane')).toContainElement(field);

    fireEvent.change(field, { target: { value: 'unblock' } });
    await waitFor(() =>
      expect(apiClient.api.ticketTree).toHaveBeenCalledWith(expect.objectContaining({ search: 'unblock' })),
    );
    expect(useUiStore.getState().search).toBe('unblock');

    fireEvent.keyDown(field, { key: 'Escape' });
    expect(useUiStore.getState().search).toBe('');
  });

  it('says what it searched for when nothing matches, and clears from there', async () => {
    renderAt('/console');
    fireEvent.change(await screen.findByRole('searchbox', { name: 'Filter work items by title or id' }), {
      target: { value: 'zzz' },
    });

    expect(await screen.findByText(/“zzz”/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear the filter' }));
    expect(useUiStore.getState().search).toBe('');
  });
});

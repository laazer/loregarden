import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../../api/client';
import { githubIssueApi, type GithubWorkspaceSyncResult } from '../../api/githubIssueApi';
import { useUiStore } from '../../state/uiStore';
import { GithubSyncModal } from '../GithubSyncModal';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));
jest.mock('../../api/githubIssueApi', () => ({
  githubIssueApi: { syncWorkspace: jest.fn() },
}));
// The parent picker fetches a ticket tree of its own; it is not what is under test.
jest.mock('../ParentTicketSelector', () => ({
  ParentTicketSelector: ({ workspaceSlug, onChange }: { workspaceSlug: string; onChange: (id: string) => void }) => (
    <button type="button" onClick={() => onChange(`parent-in-${workspaceSlug}`)}>
      Pick parent
    </button>
  ),
}));

const mockedSync = githubIssueApi.syncWorkspace as jest.Mock;
const mockedWorkspaces = api.workspaces as jest.Mock;

const workspace = (slug: string) => ({ id: slug, slug, name: slug }) as never;

function result(over: Partial<GithubWorkspaceSyncResult> = {}): GithubWorkspaceSyncResult {
  return { workspace_slug: 'beta', repo: 'acme/widgets', links: [], imported: [], ...over };
}

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <GithubSyncModal open onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.resetAllMocks();
  mockedWorkspaces.mockResolvedValue([workspace('alpha'), workspace('beta')]);
  useUiStore.setState({ workspace: 'beta' });
});

it('syncs the active workspace without importing by default', async () => {
  mockedSync.mockResolvedValue(result());

  renderModal();
  await screen.findByRole('button', { name: 'Pick parent' });
  fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));

  await waitFor(() =>
    expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: '', import_label: '' }),
  );
  expect(await screen.findByText(/no ticket in this workspace is linked yet/)).toBeInTheDocument();
});

it('imports under the chosen parent and reports each row', async () => {
  mockedSync.mockResolvedValue(
    result({
      links: [
        {
          ticket_id: 't1', external_id: 'lor-1', issue_number: 3, issue_url: 'https://x/3',
          pushed: [], pulled: ['title'], conflicts: [], error: '',
        },
        {
          ticket_id: 't2', external_id: 'lor-2', issue_number: 4, issue_url: 'https://x/4',
          pushed: [], pulled: [], conflicts: [], error: 'HTTP 404',
        },
      ],
    }),
  );

  renderModal();
  fireEvent.click(await screen.findByRole('button', { name: 'Pick parent' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sync and import' }));

  await waitFor(() =>
    expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: 'parent-in-beta', import_label: '' }),
  );
  expect(await screen.findByText(/Pulled title from GitHub/)).toBeInTheDocument();
  expect(screen.getByText('Failed: HTTP 404')).toBeInTheDocument();
  expect(screen.getByText(/1 need attention/)).toBeInTheDocument();
});

it('shows a failed sync inline', async () => {
  mockedSync.mockRejectedValue(new Error('gh is not logged in'));

  renderModal();
  await screen.findByRole('button', { name: 'Pick parent' });
  fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Sync failed: gh is not logged in');
});

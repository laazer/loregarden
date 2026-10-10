import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../../api/client';
import {
  githubIssueApi,
  type GithubSyncSettings,
  type GithubWorkspaceSyncResult,
} from '../../api/githubIssueApi';
import { useUiStore } from '../../state/uiStore';
import { GithubSyncModal } from '../GithubSyncModal';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));
jest.mock('../../api/githubIssueApi', () => ({
  githubIssueApi: { syncWorkspace: jest.fn(), syncSettings: jest.fn(), saveSyncSettings: jest.fn() },
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
const mockedSettings = githubIssueApi.syncSettings as jest.Mock;
const mockedSave = githubIssueApi.saveSyncSettings as jest.Mock;

function settings(over: Partial<GithubSyncSettings> = {}): GithubSyncSettings {
  return {
    workspace_slug: 'beta',
    enabled: false,
    interval_minutes: 15,
    push_on_edit: false,
    import_parent_ticket_id: '',
    import_label: '',
    last_run_at: null,
    last_error: '',
    ...over,
  };
}
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
  mockedSettings.mockResolvedValue(settings());
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

it('turns background sync on with the chosen interval and import parent', async () => {
  mockedSave.mockImplementation((_slug: string, body: Partial<GithubSyncSettings>) =>
    Promise.resolve(settings(body)),
  );

  renderModal();
  fireEvent.click(await screen.findByLabelText('Sync this workspace automatically'));
  fireEvent.change(screen.getByLabelText('How often'), { target: { value: '60' } });
  fireEvent.click(screen.getByRole('button', { name: 'Pick parent' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

  await waitFor(() =>
    expect(mockedSave).toHaveBeenCalledWith('beta', {
      enabled: true,
      interval_minutes: 60,
      push_on_edit: false,
      import_parent_ticket_id: 'parent-in-beta',
      import_label: '',
    }),
  );
  expect(await screen.findByRole('button', { name: 'Saved' })).toBeDisabled();
});

it('shows the last background failure and prefills the saved import parent', async () => {
  mockedSettings.mockResolvedValue(
    settings({
      enabled: true,
      import_parent_ticket_id: 'saved-parent',
      last_run_at: '2026-09-27T10:00:00Z',
      last_error: 'gh: not logged in',
    }),
  );
  mockedSync.mockResolvedValue(result());

  renderModal();

  expect(await screen.findByText('Last background run failed: gh: not logged in')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sync and import' }));
  await waitFor(() =>
    expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: 'saved-parent', import_label: '' }),
  );
});

it('turns push-on-edit on without the schedule', async () => {
  mockedSave.mockImplementation((_slug: string, body: Partial<GithubSyncSettings>) =>
    Promise.resolve(settings(body)),
  );

  renderModal();
  fireEvent.click(await screen.findByLabelText('Push ticket edits to GitHub as they happen'));
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

  await waitFor(() =>
    expect(mockedSave).toHaveBeenCalledWith('beta', expect.objectContaining({ enabled: false, push_on_edit: true })),
  );
});

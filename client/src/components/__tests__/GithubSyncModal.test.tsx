import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { api } from '../../api/client';
import {
  githubIssueApi,
  type GithubSyncSettings,
  type GithubWorkspaceSyncResult,
} from '../../api/githubIssueApi';
import { useTicketRefStore } from '../../state/ticketRefStore';
import { useToastStore } from '../../state/toastStore';
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
const mockedTicket = api.ticket as jest.Mock;

const OPEN_UUID = '11111111-2222-4333-8444-555555555555';
function openTicket(over: Record<string, unknown> = {}) {
  return {
    id: OPEN_UUID, external_id: 'lor-auth-7', title: 'Auth', workspace_slug: 'alpha',
    work_item_type: 'feature', ...over,
  };
}

const workspace = (slug: string) => ({ id: slug, slug, name: slug }) as never;

function result(over: Partial<GithubWorkspaceSyncResult> = {}): GithubWorkspaceSyncResult {
  return { workspace_slug: 'beta', repo: 'acme/widgets', links: [], imported: [], ...over };
}

function renderModal(path = '/chat') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <GithubSyncModal open onClose={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.resetAllMocks();
  mockedWorkspaces.mockResolvedValue([workspace('alpha'), workspace('beta')]);
  mockedSettings.mockResolvedValue(settings());
  useUiStore.setState({ workspace: 'beta' });
  useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
  useToastStore.setState({ toasts: [] });
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

describe('with a ticket open', () => {
  beforeEach(() => {
    mockedSettings.mockImplementation((slug: string) =>
      Promise.resolve(settings({ workspace_slug: slug, import_parent_ticket_id: `saved-in-${slug}` })),
    );
    mockedSync.mockResolvedValue(result());
  });

  it('preselects its workspace and the ticket, by UUID, when its shareable id is in the URL', async () => {
    mockedTicket.mockResolvedValue(openTicket());

    renderModal('/tickets/lor-auth-7/diff');
    fireEvent.click(await screen.findByRole('button', { name: 'Sync and import' }));

    expect(mockedTicket).toHaveBeenCalledWith('lor-auth-7');
    expect(screen.getByLabelText('Workspace')).toHaveValue('alpha');
    await waitFor(() =>
      expect(mockedSync).toHaveBeenCalledWith('alpha', { import_parent_ticket_id: OPEN_UUID, import_label: '' }),
    );
    expect(screen.queryByText(/saved default is used/)).not.toBeInTheDocument();
  });

  it('falls back to the saved default, and says why, when the ticket cannot hold bugs', async () => {
    mockedTicket.mockResolvedValue(openTicket({ work_item_type: 'task' }));

    renderModal(`/tickets/${OPEN_UUID}/diff`);
    fireEvent.click(await screen.findByRole('button', { name: 'Sync and import' }));

    expect(screen.getByText(/lor-auth-7, is a task, which can't hold bugs/)).toBeInTheDocument();
    await waitFor(() =>
      expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: 'saved-in-beta', import_label: '' }),
    );
  });

  it('falls back to the saved default and reports it when the ticket cannot be loaded', async () => {
    mockedTicket.mockRejectedValue(new Error('HTTP 500'));

    renderModal('/tickets/lor-auth-7/diff');
    fireEvent.click(await screen.findByRole('button', { name: 'Sync and import' }));

    await waitFor(() =>
      expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: 'saved-in-beta', import_label: '' }),
    );
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({ tone: 'error', message: expect.stringContaining('HTTP 500') }),
    ]);
  });

  it('does not save the open ticket as the default unless the operator saves', async () => {
    mockedTicket.mockResolvedValue(openTicket());

    renderModal('/tickets/lor-auth-7/diff');
    await screen.findByRole('button', { name: 'Sync and import' });

    expect(mockedSave).not.toHaveBeenCalled();
    // The fields now differ from what is saved, so the save offers to store them.
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
  });

  it('lets a manual workspace change clear the ticket and use that workspace default', async () => {
    mockedTicket.mockResolvedValue(openTicket());

    renderModal('/tickets/lor-auth-7/diff');
    await screen.findByRole('button', { name: 'Sync and import' });
    fireEvent.change(screen.getByLabelText('Workspace'), { target: { value: 'beta' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Pick parent' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sync and import' }));

    await waitFor(() =>
      expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: 'parent-in-beta', import_label: '' }),
    );
  });
});

it('leaves the saved default in place with no ticket open', async () => {
  mockedSettings.mockResolvedValue(settings({ import_parent_ticket_id: 'saved-parent' }));
  mockedSync.mockResolvedValue(result());

  renderModal('/queue');
  fireEvent.click(await screen.findByRole('button', { name: 'Sync and import' }));

  expect(mockedTicket).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(mockedSync).toHaveBeenCalledWith('beta', { import_parent_ticket_id: 'saved-parent', import_label: '' }),
  );
});

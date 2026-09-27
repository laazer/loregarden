import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import type * as apiClient from '../../api/client';
import { githubIssueApi, type GithubLinkSyncResult } from '../../api/githubIssueApi';
import { TicketGithubIssue } from '../TicketGithubIssue';

jest.mock('../../api/githubIssueApi', () => ({
  githubIssueApi: {
    link: jest.fn(),
    publish: jest.fn(),
    sync: jest.fn(),
    unlink: jest.fn(),
  },
}));

const mockedApi = githubIssueApi as jest.Mocked<typeof githubIssueApi>;

const ticket = { id: 'ticket-1', work_item_type: 'bug' } as apiClient.TicketDetail;

const link = {
  ticket_id: 'ticket-1',
  repo: 'acme/widgets',
  issue_number: 7,
  issue_url: 'https://github.com/acme/widgets/issues/7',
  last_synced_at: '2026-09-26T12:00:00Z',
  last_error: '',
};

function result(over: Partial<GithubLinkSyncResult> = {}): GithubLinkSyncResult {
  return {
    ticket_id: 'ticket-1',
    external_id: 'lor-1',
    issue_number: 7,
    issue_url: link.issue_url,
    pushed: [],
    pulled: [],
    conflicts: [],
    error: '',
    ...over,
  };
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TicketGithubIssue ticket={ticket} />
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.resetAllMocks());

it('offers to publish an unlinked ticket', async () => {
  mockedApi.link.mockResolvedValue(null);
  mockedApi.publish.mockResolvedValue(result());

  renderCard();
  fireEvent.click(await screen.findByRole('button', { name: 'Publish to GitHub' }));

  await waitFor(() => expect(mockedApi.publish).toHaveBeenCalledWith('ticket-1'));
});

it('shows the linked issue and what a sync moved', async () => {
  mockedApi.link.mockResolvedValue(link);
  mockedApi.sync.mockResolvedValue(result({ pulled: ['title'] }));

  renderCard();
  expect(await screen.findByRole('link', { name: 'acme/widgets#7' })).toHaveAttribute('href', link.issue_url);
  fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));

  expect(await screen.findByText(/Pulled title from GitHub/)).toBeInTheDocument();
  expect(mockedApi.sync).toHaveBeenCalledWith('ticket-1', 'report');
});

it('lets the operator settle a conflict by picking a side', async () => {
  mockedApi.link.mockResolvedValue(link);
  mockedApi.sync.mockResolvedValueOnce(
    result({ conflicts: [{ field: 'title', base: 'a', local: 'b', remote: 'c' }] }),
  );
  mockedApi.sync.mockResolvedValueOnce(result({ pushed: ['title'] }));

  renderCard();
  fireEvent.click(await screen.findByRole('button', { name: 'Sync now' }));
  fireEvent.click(await screen.findByRole('button', { name: "Keep ticket's version" }));

  await waitFor(() => expect(mockedApi.sync).toHaveBeenLastCalledWith('ticket-1', 'local'));
});

it('shows a failed link read instead of an empty card', async () => {
  mockedApi.link.mockRejectedValue(new Error('server down'));

  renderCard();

  expect(await screen.findByText(/Could not read the GitHub link: server down/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Publish to GitHub' })).not.toBeInTheDocument();
});

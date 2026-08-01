/**
 * The dispatch CTA.
 *
 * POST /api/parallel/runs/{ticket_id} existed since parallel execution shipped
 * and nothing ever called it. What matters here is that it is called against
 * the absolute API base, and that a successful dispatch does not write local
 * queue state — the socket reports what the server actually did.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import { API_BASE } from '../../api/client';
import { QueueDispatchButton } from '../QueueDispatchButton';
import { useQueueStatus } from '../../state/QueueStatusContext';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));

jest.mock('../../api/client', () => ({
  API_BASE: 'http://127.0.0.1:8000',
  api: { tickets: jest.fn() },
}));

import { api } from '../../api/client';

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;
const mockTickets = api.tickets as jest.Mock;

const TICKETS = [
  { id: 'ticket-1', external_id: 'LG-101', title: 'Bootstrap vertical slice', state: 'backlog' },
  { id: 'ticket-2', external_id: 'LG-102', title: 'Wire the approval gate', state: 'in_progress' },
];

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueueDispatchButton />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockUseQueueStatus.mockReturnValue({ activeSlug: 'loregarden' } as ReturnType<
    typeof useQueueStatus
  >);
  mockTickets.mockResolvedValue(TICKETS);
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
});

test('does not fetch tickets until the picker is opened', () => {
  renderButton();

  expect(mockTickets).not.toHaveBeenCalled();
});

test('lists dispatchable tickets once opened', async () => {
  renderButton();

  fireEvent.click(screen.getByText('Dispatch run'));

  expect(await screen.findByText('Bootstrap vertical slice')).toBeInTheDocument();
  expect(screen.getByText('LG-101')).toBeInTheDocument();
  // Blocked and finished work has nothing left to dispatch.
  expect(mockTickets).toHaveBeenCalledWith({
    workspace: 'loregarden',
    state: ['backlog', 'in_progress'],
  });
});

test('dispatches against the absolute API base', async () => {
  renderButton();

  fireEvent.click(screen.getByText('Dispatch run'));
  fireEvent.click(await screen.findByText('Bootstrap vertical slice'));

  await waitFor(() =>
    expect(global.fetch).toHaveBeenCalledWith(
      `${API_BASE}/api/parallel/runs/ticket-1`,
      expect.objectContaining({ method: 'POST' }),
    ),
  );
});

test('closes the picker after a successful dispatch', async () => {
  renderButton();

  fireEvent.click(screen.getByText('Dispatch run'));
  fireEvent.click(await screen.findByText('Bootstrap vertical slice'));

  await waitFor(() =>
    expect(screen.queryByText('Bootstrap vertical slice')).not.toBeInTheDocument(),
  );
});

test('surfaces the server detail and stays open when dispatch fails', async () => {
  (global.fetch as jest.Mock).mockResolvedValue({
    ok: false,
    status: 409,
    json: async () => ({ detail: 'Ticket already has an active run' }),
  });

  renderButton();

  fireEvent.click(screen.getByText('Dispatch run'));
  fireEvent.click(await screen.findByText('Bootstrap vertical slice'));

  expect(await screen.findByText('Ticket already has an active run')).toBeInTheDocument();
  expect(screen.getByText('Bootstrap vertical slice')).toBeInTheDocument();
});

test('says so when there is nothing to dispatch', async () => {
  mockTickets.mockResolvedValue([]);

  renderButton();
  fireEvent.click(screen.getByText('Dispatch run'));

  expect(await screen.findByText('No tickets ready to dispatch')).toBeInTheDocument();
});

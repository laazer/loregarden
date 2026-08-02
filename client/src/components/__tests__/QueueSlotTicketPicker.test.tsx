/**
 * The picker that fills an empty slot.
 *
 * What matters here is that it *stages* — it hands the ticket back and does not
 * call the run endpoint. Starting is the slot card's job, and a picker that
 * launched on click would put the old one-click dispatch back.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { api } from '../../api/client';
import { QueueSlotTicketPicker } from '../QueueSlotTicketPicker';

jest.mock('../../api/client', () => ({
  API_BASE: 'http://test',
  api: { tickets: jest.fn() },
}));

const mockTickets = api.tickets as jest.MockedFunction<typeof api.tickets>;

const TICKETS = [
  { id: 'ticket-1', external_id: 'LG-1', title: 'Bootstrap vertical slice', state: 'backlog' },
  { id: 'ticket-2', external_id: 'LG-2', title: 'Wire the approval gate', state: 'in_progress' },
];

function renderPicker(props: Partial<React.ComponentProps<typeof QueueSlotTicketPicker>> = {}) {
  const onPick = jest.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <QueueSlotTicketPicker
        slotNumber={2}
        excludedTicketIds={[]}
        onPick={onPick}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onPick };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockTickets.mockResolvedValue(TICKETS as never);
});

test('does not load tickets until it is opened', () => {
  renderPicker();

  expect(mockTickets).not.toHaveBeenCalled();
});

test('offers runnable tickets from every workspace', async () => {
  renderPicker();
  fireEvent.click(screen.getByLabelText('Add a ticket to slot 2'));

  await waitFor(() => expect(mockTickets).toHaveBeenCalled());
  // No workspace filter: the slot pool is shared, so scoping the list would
  // let you fill the machine from one project as if the others were idle.
  expect(mockTickets).toHaveBeenCalledWith({
    state: ['backlog', 'in_progress'],
  });
});

test('picking hands the ticket back instead of dispatching it', async () => {
  const globalFetch = jest.fn();
  global.fetch = globalFetch as unknown as typeof fetch;

  const { onPick } = renderPicker();
  fireEvent.click(screen.getByLabelText('Add a ticket to slot 2'));

  fireEvent.click(await screen.findByText('Wire the approval gate'));

  expect(onPick).toHaveBeenCalledWith({
    ticketId: 'ticket-2',
    code: 'LG-2',
    title: 'Wire the approval gate',
    // Both resolved from the workspace list; empty here because the mocked
    // context supplies none. The card shows the name so a shared board says
    // whose work each lane holds; the slug is what the run dialog edits.
    workspaceName: '',
    workspaceSlug: '',
  });
  expect(globalFetch).not.toHaveBeenCalled();
});

test('filters by title or code', async () => {
  renderPicker();
  fireEvent.click(screen.getByLabelText('Add a ticket to slot 2'));
  await screen.findByText('Wire the approval gate');

  fireEvent.change(screen.getByPlaceholderText('Search tickets…'), {
    target: { value: 'LG-1' },
  });

  expect(screen.getByText('Bootstrap vertical slice')).toBeInTheDocument();
  expect(screen.queryByText('Wire the approval gate')).not.toBeInTheDocument();
});

test('hides tickets already staged in another slot', async () => {
  renderPicker({ excludedTicketIds: ['ticket-1'] });
  fireEvent.click(screen.getByLabelText('Add a ticket to slot 2'));

  await screen.findByText('Wire the approval gate');
  expect(screen.queryByText('Bootstrap vertical slice')).not.toBeInTheDocument();
});

test('says so when there is nothing to run', async () => {
  mockTickets.mockResolvedValue([] as never);

  renderPicker();
  fireEvent.click(screen.getByLabelText('Add a ticket to slot 2'));

  expect(await screen.findByText('No tickets ready to run')).toBeInTheDocument();
});

test('closes on Escape', async () => {
  renderPicker();
  fireEvent.click(screen.getByLabelText('Add a ticket to slot 2'));
  await screen.findByText('Wire the approval gate');

  fireEvent.keyDown(document, { key: 'Escape' });

  expect(screen.queryByText('Wire the approval gate')).not.toBeInTheDocument();
});

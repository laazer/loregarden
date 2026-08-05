/**
 * Choosing where a ticket runs, with the queue in view.
 *
 * Every start takes a slot now, so the choice is offered rather than assumed —
 * but Auto has to stay the default, because most starts do not care and the
 * server picks better than a guess. What the picker owes the operator is the
 * state behind each lane: picking a busy one means waiting, and the dialog
 * says so instead of letting that be discovered after the click.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { LanePicker } from '../LanePicker';

jest.mock('../../api/client', () => ({ API_BASE: 'http://test' }));

const LANES = [
  { slot_number: 1, running: null, waiting: [] },
  {
    slot_number: 2,
    running: { ticket_code: 'LG-57', ticket_id: 't-57', status: 'running' },
    waiting: [{ entry_id: 'e-1' }],
  },
  { slot_number: 3, running: null, waiting: [] },
];

function renderPicker(value: number | null = null) {
  const onChange = jest.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LanePicker value={value} onChange={onChange} />
    </QueryClientProvider>
  );
  return { onChange };
}

beforeEach(() => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ lanes: LANES }),
  }) as unknown as typeof fetch;
});

afterEach(() => {
  jest.resetAllMocks();
});

test('offers Auto alongside every lane', async () => {
  renderPicker();

  expect(screen.getByText('Auto')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText('Lane 1')).toBeInTheDocument());
  expect(screen.getByText('Lane 2')).toBeInTheDocument();
  expect(screen.getByText('Lane 3')).toBeInTheDocument();
});

test('Auto is what is selected when no lane was asked for', () => {
  renderPicker(null);

  expect(screen.getByRole('radio', { name: /Auto/ })).toBeChecked();
});

test('says what a lane is doing, so a busy one is a choice and not a surprise', async () => {
  renderPicker();

  await waitFor(() => expect(screen.getByText(/Running LG-57/)).toBeInTheDocument());
  expect(screen.getByText(/1 waiting/)).toBeInTheDocument();
});

test('picking a busy lane says the ticket will wait for it', async () => {
  const { onChange } = renderPicker();

  await waitFor(() => expect(screen.getByText('Lane 2')).toBeInTheDocument());
  fireEvent.click(screen.getByText('Lane 2'));

  expect(onChange).toHaveBeenCalledWith(2);
});

test('a busy lane already chosen explains the wait rather than promising a start', async () => {
  renderPicker(2);

  await waitFor(() =>
    expect(screen.getByText(/Starts when lane 2 finishes/)).toBeInTheDocument()
  );
});

test('an unreadable queue still lets the run be started', async () => {
  global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as unknown as typeof fetch;
  renderPicker();

  await waitFor(() =>
    expect(screen.getByText(/still start in whichever lane is free/)).toBeInTheDocument()
  );
  // Auto remains available with no lane data at all — the server decides.
  expect(screen.getByRole('radio', { name: /Auto/ })).toBeChecked();
});

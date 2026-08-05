/**
 * The history rail keeps finished lane entries findable.
 *
 * The point of the card is the outcome badge: the raw entry status of a
 * finished run is "started", which reads as still-running. A card that showed
 * it would be worse than no card.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { QueueHistoryRail } from '../QueueHistoryRail';

const navigateToTicket = jest.fn();
jest.mock('../../lib/useAppNavigation', () => ({
  navigateToTicket: (...args: unknown[]) => navigateToTicket(...args),
}));

const blockedEntry = {
  entry_id: 'entry-1',
  workspace_id: 'ws-1',
  slot_number: 2,
  entry_kind: 'orchestration',
  stage_key: '',
  status: 'started',
  outcome: 'blocked',
  ticket_id: 'ticket-1',
  ticket_external_id: '57-generate-checkpoints',
  ticket_title: 'Generate CHECKPOINTS.md',
  ticket_state: 'blocked',
  orchestration_run_id: 'orch-1',
  run_code: 'orch_d7253c',
  last_stage_key: 'test_design',
  failure_reason: 'Child ticket blocked: jailed creature persistence',
  retry_count: 1,
  created_at: '2026-08-05T11:00:00Z',
  promoted_at: '2026-08-05T11:45:00Z',
  started_at: '2026-08-05T11:45:00Z',
  finished_at: '2026-08-05T11:50:00Z',
  duration_seconds: 300,
};

function mockPage(entries: unknown[]) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ entries, total: entries.length, limit: 25, offset: 0 }),
  });
}

describe('QueueHistoryRail', () => {
  beforeEach(() => {
    navigateToTicket.mockClear();
    mockPage([blockedEntry]);
  });

  it('badges a released entry with its outcome, not its queue status', async () => {
    render(<QueueHistoryRail workspaceId="ws-1" />);

    expect(await screen.findByText('Blocked')).toBeInTheDocument();
    expect(screen.queryByText(/started/i)).not.toBeInTheDocument();
  });

  it('shows the stage, duration, retries and failure reason', async () => {
    render(<QueueHistoryRail workspaceId="ws-1" />);

    expect(await screen.findByText('Generate CHECKPOINTS.md')).toBeInTheDocument();
    expect(
      screen.getByText(/57-generate-checkpoints · slot 2 · test_design · 5m · 1 retries/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Child ticket blocked: jailed creature persistence'),
    ).toBeInTheDocument();
  });

  it('navigates to the ticket a card names', async () => {
    render(<QueueHistoryRail workspaceId="ws-1" />);

    fireEvent.click(await screen.findByText('Generate CHECKPOINTS.md'));

    expect(navigateToTicket).toHaveBeenCalledWith('ticket-1');
  });

  it('refetches with the outcome filter the operator picked', async () => {
    render(<QueueHistoryRail workspaceId="ws-1" />);
    await screen.findByText('Blocked');

    fireEvent.click(screen.getByRole('button', { name: 'Failed' }));

    await waitFor(() => {
      const urls = (global.fetch as jest.Mock).mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('outcome=failed'))).toBe(true);
    });
  });

  it('says so when no lane has run anything', async () => {
    mockPage([]);
    render(<QueueHistoryRail workspaceId="ws-1" />);

    expect(await screen.findByText('Nothing has run through a lane yet.')).toBeInTheDocument();
  });
});

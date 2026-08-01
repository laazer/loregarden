/**
 * The queue header, now that it renders in the top action bar.
 *
 * The metrics come from QueueStatusContext rather than a subscription of their
 * own — that is the whole reason the provider sits above the layout — so these
 * tests drive the context directly.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { QueueTopbarControls } from '../QueueTopbarControls';
import { useQueueStatus, type QueueStatusValue } from '../../state/QueueStatusContext';

jest.mock('../../state/QueueStatusContext', () => ({
  useQueueStatus: jest.fn(),
}));

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

const setWorkspaceSlug = jest.fn();

const baseStatus: QueueStatusValue = {
  workspace: null,
  workspaces: [
    { slug: 'loregarden', name: 'loregarden' },
    { slug: 'blobert', name: 'blobert' },
  ] as QueueStatusValue['workspaces'],
  workspacesLoading: false,
  activeSlug: 'loregarden',
  setWorkspaceSlug,
  activeRuns: [],
  queuedRuns: [],
  stats: {
    max_concurrent: 3,
    active_count: 1,
    available_slots: 2,
    queued_count: 2,
    total_slots_occupied: 1,
    queue_wait_time_minutes: 0,
  },
  estimatedClearSeconds: null,
  isWebSocket: true,
  loading: false,
  onQueueEvent: jest.fn(() => () => {}),
};

const withStatus = (overrides: Partial<QueueStatusValue> = {}) => {
  mockUseQueueStatus.mockReturnValue({ ...baseStatus, ...overrides });
};

beforeEach(() => {
  jest.clearAllMocks();
  withStatus();
});

test('shows utilization, active slots and queue depth', () => {
  render(<QueueTopbarControls />);

  expect(screen.getByText('33%')).toBeInTheDocument();
  expect(screen.getByText('1/3')).toBeInTheDocument();
  expect(screen.getByText('2')).toBeInTheDocument();
});

test('reports a live socket', () => {
  render(<QueueTopbarControls />);

  expect(screen.getByText('Real-time')).toBeInTheDocument();
});

test('reports the polling fallback honestly', () => {
  withStatus({ isWebSocket: false });

  render(<QueueTopbarControls />);

  expect(screen.getByText('Polling')).toBeInTheDocument();
  expect(screen.queryByText('Real-time')).not.toBeInTheDocument();
});

test('shows zero utilization rather than dividing by nothing', () => {
  withStatus({
    stats: { ...baseStatus.stats, active_count: 0, max_concurrent: 0 },
  });

  render(<QueueTopbarControls />);

  expect(screen.getByText('0%')).toBeInTheDocument();
});

test('lists the workspaces and reports a change', () => {
  render(<QueueTopbarControls />);

  const picker = screen.getByLabelText('Queue workspace');
  expect(picker).toHaveValue('loregarden');

  fireEvent.change(picker, { target: { value: 'blobert' } });

  expect(setWorkspaceSlug).toHaveBeenCalledWith('blobert');
});

test('disables the picker when there are no workspaces', () => {
  withStatus({ workspaces: [], activeSlug: '' });

  render(<QueueTopbarControls />);

  expect(screen.getByLabelText('Queue workspace')).toBeDisabled();
});

import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { QueueLaneCount } from '../QueueLaneCount';
import { queueLanesApi } from '../../lib/queueLanesApi';
import { toastActionFailed } from '../../state/toastStore';

jest.mock('../../lib/queueLanesApi', () => ({
  queueLanesApi: { laneCount: jest.fn(), setLaneCount: jest.fn() },
}));
jest.mock('../../state/toastStore', () => {
  const actual = jest.requireActual('../../state/toastStore');
  return { ...actual, toastActionFailed: jest.fn() };
});

const laneCount = queueLanesApi.laneCount as jest.Mock;
const setLaneCount = queueLanesApi.setLaneCount as jest.Mock;

describe('QueueLaneCount', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    laneCount.mockResolvedValue({ lane_count: 3, min: 1, max: 12 });
  });

  it('saves a new count and says which lanes are winding down', async () => {
    setLaneCount.mockResolvedValue({ lane_count: 2, retiring_lanes: [3], moved_entries: 1 });
    render(<QueueLaneCount />);

    const input = await screen.findByLabelText('Run at once');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();

    fireEvent.change(input, { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(setLaneCount).toHaveBeenCalledWith(2));
    expect(await screen.findByText(/Lane 3 finishes its current ticket/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('will not save a count outside the range', async () => {
    render(<QueueLaneCount />);
    const input = await screen.findByLabelText('Run at once');

    fireEvent.change(input, { target: { value: '13' } });

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByText('Enter a whole number from 1 to 12.')).toBeInTheDocument();
  });

  it('surfaces a failed save', async () => {
    setLaneCount.mockRejectedValue(new Error('boom'));
    render(<QueueLaneCount />);
    fireEvent.change(await screen.findByLabelText('Run at once'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(toastActionFailed).toHaveBeenCalledWith('Change lane count', expect.any(Error)),
    );
  });

  it('offers a retry when the count cannot be read', async () => {
    laneCount.mockRejectedValueOnce(new Error('down'));
    render(<QueueLaneCount />);

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

    expect(await screen.findByLabelText('Run at once')).toHaveValue(3);
  });
});

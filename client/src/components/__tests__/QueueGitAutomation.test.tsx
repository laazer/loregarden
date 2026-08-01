/**
 * The git automation toggles.
 *
 * The behaviour worth pinning is the chain: the server stops at the first step
 * that is off, so the UI must not let someone tick a step whose predecessor is
 * off and believe it will happen.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import { api } from '../../api/client';
import type { GitAutomationView } from '../../api/types';
import { QueueGitAutomation } from '../QueueGitAutomation';

jest.mock('../../api/client', () => ({
  api: { gitAutomation: jest.fn(), updateGitAutomation: jest.fn() },
}));

const mockGet = api.gitAutomation as jest.Mock;
const mockUpdate = api.updateGitAutomation as jest.Mock;

const CONFIG: GitAutomationView = {
  worktree: true,
  commit: false,
  push: false,
  open_pr: false,
  auto_merge: false,
  auto_resolve_conflicts: false,
  max_conflict_resolve_attempts: 2,
  base_branch: 'main',
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueueGitAutomation workspaceSlug="proj" />
    </QueryClientProvider>,
  );
}

/** The checkbox belonging to the row with this exact label. */
const toggle = (label: string): HTMLInputElement => {
  const row = screen.getByText(label, { selector: '.queue-git-toggle-label' });
  return row.closest('label')!.querySelector('input')!;
};

beforeEach(() => {
  jest.clearAllMocks();
  mockGet.mockResolvedValue(CONFIG);
  mockUpdate.mockImplementation((_slug: string, body: GitAutomationView) =>
    Promise.resolve(body),
  );
});

test('renders every step as its own switch', async () => {
  renderPanel();

  expect(await screen.findByText('Run in a worktree')).toBeInTheDocument();
  expect(screen.getByText('Commit')).toBeInTheDocument();
  expect(screen.getByText('Push')).toBeInTheDocument();
  expect(screen.getByText('Open a pull request')).toBeInTheDocument();
  expect(screen.getByText('Auto-merge')).toBeInTheDocument();
  expect(screen.getByText('Auto-resolve conflicts')).toBeInTheDocument();
});

test('a step whose predecessor is off cannot be ticked', async () => {
  renderPanel();
  await screen.findByText('Commit');

  // Commit is off, so push would be skipped server-side; offering it would
  // claim the queue does something it does not.
  expect(toggle('Push')).toBeDisabled();
  expect(toggle('Auto-merge')).toBeDisabled();
  // Both say why, rather than just going grey.
  expect(screen.getAllByText('Needs "Commit" first.')).toHaveLength(2);
});

test('enabling a step unlocks the next one', async () => {
  mockGet.mockResolvedValue({ ...CONFIG, commit: true });

  renderPanel();
  await screen.findByText('Commit');

  expect(toggle('Push')).not.toBeDisabled();
  expect(toggle('Open a pull request')).toBeDisabled();
});

test('auto-merge needs commits, not a pull request', async () => {
  // With a PR it enables GitHub auto-merge; without one it merges the run's
  // worktree branch locally. Gating it on the PR would make the local path
  // unreachable from the UI.
  mockGet.mockResolvedValue({ ...CONFIG, commit: true });

  renderPanel();
  await screen.findByText('Commit');

  expect(toggle('Auto-merge')).not.toBeDisabled();
});

test('saves the change to the workspace profile', async () => {
  renderPanel();
  await screen.findByText('Commit');

  fireEvent.click(toggle('Commit'));

  await waitFor(() =>
    expect(mockUpdate).toHaveBeenCalledWith('proj', expect.objectContaining({ commit: true })),
  );
});

test('turning a step off clears everything downstream', async () => {
  mockGet.mockResolvedValue({
    ...CONFIG,
    commit: true,
    push: true,
    open_pr: true,
    auto_merge: true,
    auto_resolve_conflicts: true,
  });

  renderPanel();
  await screen.findByText('Push');

  fireEvent.click(toggle('Push'));

  // Leaving the downstream flags set would show a config the server ignores.
  // Auto-merge is not downstream of push — without a PR it merges locally —
  // so it survives, and auto-resolve with it.
  await waitFor(() =>
    expect(mockUpdate).toHaveBeenCalledWith('proj', {
      ...CONFIG,
      commit: true,
      push: false,
      open_pr: false,
      auto_merge: true,
      auto_resolve_conflicts: true,
    }),
  );
});

test('turning off commit clears every step that needs it', async () => {
  mockGet.mockResolvedValue({
    ...CONFIG,
    commit: true,
    push: true,
    open_pr: true,
    auto_merge: true,
    auto_resolve_conflicts: true,
  });

  renderPanel();
  await screen.findByText('Commit');

  fireEvent.click(toggle('Commit'));

  await waitFor(() =>
    expect(mockUpdate).toHaveBeenCalledWith('proj', {
      ...CONFIG,
      commit: false,
      push: false,
      open_pr: false,
      auto_merge: false,
      auto_resolve_conflicts: false,
    }),
  );
});

test('the resolution budget only appears when auto-resolve is on', async () => {
  renderPanel();
  await screen.findByText('Commit');
  expect(screen.queryByText('Resolution attempts')).not.toBeInTheDocument();

  mockGet.mockResolvedValue({
    ...CONFIG,
    commit: true,
    push: true,
    open_pr: true,
    auto_merge: true,
    auto_resolve_conflicts: true,
  });
  renderPanel();

  expect(await screen.findAllByText('Resolution attempts')).not.toHaveLength(0);
});

test('reports a save failure rather than showing a stale state as saved', async () => {
  mockUpdate.mockRejectedValue(new Error('profile is read-only'));

  renderPanel();
  await screen.findByText('Commit');

  fireEvent.click(toggle('Commit'));

  expect(await screen.findByText(/Could not save/)).toBeInTheDocument();
});

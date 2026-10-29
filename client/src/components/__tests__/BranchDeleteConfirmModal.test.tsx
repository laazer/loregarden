import { fireEvent, render, screen } from "@testing-library/react";

import { BranchDeleteConfirmModal } from "../BranchDeleteConfirmModal";
import type { BranchTriageEntry } from "../../lib/branchTriageApi";

function entry(overrides: Partial<BranchTriageEntry> = {}): BranchTriageEntry {
  return {
    name: "feature/x",
    is_current: false,
    is_base: false,
    squash_merged: false,
    commits_only_here: 0,
    ahead: 2,
    behind: 0,
    dirty: false,
    upstream: null,
    diff_options: [],
    worktrees: [],
    linked_tickets: [],
    last_commit: { date: new Date().toISOString(), message: "work" },
    issues: [],
    pr: null,
    ...overrides,
  };
}

function renderModal(branch: BranchTriageEntry) {
  const onConfirm = jest.fn();
  render(
    <BranchDeleteConfirmModal open branch={branch} isDeleting={false} onClose={jest.fn()} onConfirm={onConfirm} />,
  );
  return { onConfirm };
}

test("commits that exist nowhere else are discarded only after the person ticks the box naming them", () => {
  const { onConfirm } = renderModal(entry({ commits_only_here: 2 }));

  const submit = screen.getByRole("button", { name: "Delete branch" });
  expect(submit).toBeDisabled();

  fireEvent.click(screen.getByRole("checkbox", { name: "Discard 2 commits with the branch" }));
  expect(submit).toBeEnabled();
  fireEvent.click(submit);

  expect(onConfirm).toHaveBeenCalledWith(true);
});

test("a branch whose commits all live elsewhere deletes without a discard", () => {
  const { onConfirm } = renderModal(entry({ commits_only_here: 0 }));

  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.getByText(/deleting the branch loses none/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete branch" }));

  expect(onConfirm).toHaveBeenCalledWith(false);
});

test("an unknown count is treated as commits that would be lost", () => {
  renderModal(entry({ commits_only_here: null }));

  expect(screen.getByRole("button", { name: "Delete branch" })).toBeDisabled();
  expect(
    screen.getByRole("checkbox", { name: "Delete anyway, discarding any commits only this branch has" }),
  ).toBeInTheDocument();
});

/**
 * The branch-checkout confirmation, and specifically its body's keyboard path.
 *
 * lg-milestone-that-2215: `.modal-body` is bounded with `overflow: auto` — and
 * this one genuinely overflows, because it explains what switching the local
 * checkout does to anything running against the workspace's files. Every
 * control sits OUTSIDE the body, so nothing in it could take the focus the
 * arrow keys scroll from.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BranchCheckoutConfirmModal } from "../BranchCheckoutConfirmModal";
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
    last_commit: { date: "2026-11-01T10:00:00Z", message: "work" },
    issues: [],
    pr: null,
    ...overrides,
  };
}

const REGION = "What checking out this branch does";

function renderModal(overrides: { open?: boolean } = {}) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  render(
    <BranchCheckoutConfirmModal
      open={overrides.open ?? true}
      branch={entry()}
      currentBranch={entry({ name: "main", is_current: true })}
      isCheckingOut={false}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  return { onClose, onConfirm };
}

describe("the checkout dialog body's keyboard path", () => {
  it("is a tab stop that steps on to the footer's Cancel — AC3", async () => {
    renderModal();

    const body = screen.getByRole("group", { name: REGION });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("scroll-focus-ring");

    body.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("renders no labelled body while the dialog is closed — AC8", () => {
    renderModal({ open: false });

    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });

  it("lets Escape from inside the body still close the dialog — AC9", () => {
    const { onClose } = renderModal();

    const body = screen.getByRole("group", { name: REGION });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

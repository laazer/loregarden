/**
 * The workspace-fix confirm's body, and its keyboard path.
 *
 * lg-milestone-that-2215: `.modal-body` is a bounded `overflow: auto` region,
 * and this body genuinely overflows — the pull-request wording is five lines
 * explaining that a branch is cut in a throwaway worktree, that the operator's
 * own checkout is untouched, and that the push runs the other repository's
 * pre-push checks. `Cancel` and the submit are in `.modal-footer` OUTSIDE it,
 * so none of that could be scrolled from the keyboard.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WorkspaceFixDialog, type PendingFix } from "../WorkspaceFixDialog";

const PENDING: PendingFix = {
  slug: "blobert",
  name: "Blobert",
  repoRoot: "/Users/jacobbrandt/workspace/blobert",
  fix: { kind: "pull_request", installer: "docs", label: "AGENTS.md outdated…" },
};

const REGION = "What this fix does";

function renderDialog(
  overrides: { pending?: PendingFix | null; inFlight?: boolean; error?: unknown } = {},
) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  render(
    <WorkspaceFixDialog
      pending={overrides.pending === undefined ? PENDING : overrides.pending}
      inFlight={overrides.inFlight ?? false}
      error={overrides.error ?? null}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  return { onClose, onConfirm };
}

describe("the fix dialog body's keyboard path", () => {
  it("is a tab stop that steps on to the footer's Cancel — AC3", async () => {
    renderDialog();

    const body = screen.getByRole("group", { name: REGION });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("scroll-focus-ring");

    // The footer FOLLOWS the body inside the same <form>, so Tab forward is
    // the step this surface offers.
    body.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("renders no labelled body while nothing is pending — AC8", () => {
    renderDialog({ pending: null });

    // `pending` is null exactly when the dialog is closed, so there is never a
    // named region with no wording behind it.
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });

  it("lets Escape from inside the body still close the dialog — AC9", () => {
    const { onClose } = renderDialog();

    const body = screen.getByRole("group", { name: REGION });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps Escape shut while the request is away, as it already did — AC9", () => {
    const { onClose } = renderDialog({ inFlight: true });

    // `onDismiss` is undefined in flight on purpose: a dialog that closed
    // mid-push would leave the outcome with nowhere to be read. The region
    // binds no key of its own either way.
    const body = screen.getByRole("group", { name: REGION });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});

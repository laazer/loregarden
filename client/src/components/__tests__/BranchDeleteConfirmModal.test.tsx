import { fireEvent, render, screen } from "@testing-library/react";

import { BranchDeleteConfirmModal } from "../BranchDeleteConfirmModal";
import type { BranchTriageEntry } from "../../lib/branchTriageApi";
import { pushToast } from "../../state/toastStore";

/**
 * lg-external-harness-1026 — the single-branch confirm dialog, once the gate
 * can refuse the delete it is confirming.
 *
 * This dialog used to take only an `error` string, so a refusal arrived
 * looking exactly like a transport failure: red, and silent about what to do
 * next. A refusal means the gate worked and nothing was lost, which is a
 * different thing to tell someone.
 *
 * #690 — a branch whose commits exist nowhere else is discarded only after
 * the person ticks a box that names them. Without the tick the confirm stays
 * disabled, and the server would refuse anyway.
 */

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

jest.mock("../../state/toastStore", () => ({
  ...jest.requireActual("../../state/toastStore"),
  pushToast: jest.fn(),
}));

const PARKED = "/repo/.worktrees-releasing/8c2d44f1";
const RESTORED_PATH =
  "/Users/jacobbrandt/workspace/.worktrees/ticket-loregarden-blob-procedural-sdf-4-8e77a3e8";
const RESTORED_DETAIL =
  `The directory is still at ${RESTORED_PATH} and is still owned, but part of it may be gone ` +
  `and git no longer lists it as a worktree — delete it again, or remove the directory by hand.`;

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

function renderModal(props: Record<string, unknown> = {}) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  const view = render(
    <BranchDeleteConfirmModal
      open
      branch={entry()}
      isDeleting={false}
      onClose={onClose}
      onConfirm={onConfirm}
      {...props}
    />,
  );
  return { onClose, onConfirm, rerender: view.rerender };
}

function refusalBlock(): HTMLElement | null {
  return document.querySelector(".branch-cleanup-refusals");
}

function refusalHeading(): HTMLElement | null {
  return document.querySelector(".branch-cleanup-refusals .modal-section-title");
}

beforeEach(() => {
  mockNavigate.mockReset();
  (pushToast as jest.Mock).mockReset();
});

test("a refusal renders amber and says plainly that nothing was deleted", () => {
  renderModal({
    outcome: "refused",
    refusal: {
      reason: "no_receipt",
      detail: "Nothing was deleted. Land the branch or publish the tree first.",
      parked_path: "",
    },
  });

  const block = refusalBlock();
  expect(block).toBeInTheDocument();
  expect(block).toHaveAttribute("role", "status");
  expect(block).toHaveAttribute("aria-live", "polite");
  expect(block!.textContent?.toLowerCase()).toContain("nothing was deleted");
  expect(document.querySelector(".branch-triage-delete-error")).not.toBeInTheDocument();
});

test("a refusal with a linked ticket ends in a named control", () => {
  render(
    <BranchDeleteConfirmModal
      open
      branch={entry({
        linked_tickets: [
          { id: "uuid-lg-demo-3", external_id: "lg-demo-3", title: "t", state: "in_progress" },
        ],
      })}
      isDeleting={false}
      onClose={jest.fn()}
      onConfirm={jest.fn()}
      outcome="refused"
      refusal={{ reason: "no_receipt", detail: "Nothing was deleted.", parked_path: "" }}
    />,
  );

  const open = screen.getByRole("button", { name: "Open lg-demo-3" });
  fireEvent.click(open);
  expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("uuid-lg-demo-3"));
});

test("a refusal with no linked ticket names the two verbs that write a receipt", () => {
  renderModal({
    outcome: "refused",
    refusal: {
      reason: "no_receipt",
      detail: "Nothing was deleted. Land the branch or publish the tree first.",
      parked_path: "",
    },
  });

  const text = refusalBlock()!.textContent!.toLowerCase();
  expect(text).toContain("land");
  expect(text).toContain("publish");
});

test("a parked outcome is red and shows the exact path in a code element", () => {
  renderModal({
    outcome: "parked",
    refusal: {
      reason: "cleanup_failed",
      detail: `Nothing was deleted. The checkout is parked at ${PARKED} and still owned.`,
      parked_path: PARKED,
    },
  });

  expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument();
  expect([...document.querySelectorAll("code")].map((n) => n.textContent)).toContain(PARKED);
});

test("a transport error keeps the existing red block and no refusal block", () => {
  renderModal({ error: "Network request failed" });

  expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument();
  expect(refusalBlock()).not.toBeInTheDocument();
});

test("with nothing refused there is no refusal block at all", () => {
  renderModal();

  expect(refusalBlock()).not.toBeInTheDocument();
  expect(screen.queryByText(/no refusals/i)).not.toBeInTheDocument();
});

test("the confirm button stays disabled while the delete is in flight", () => {
  renderModal({ isDeleting: true });

  expect(screen.getByRole("button", { name: /Deleting/ })).toBeDisabled();
});

test.each(["refused", "parked"])(
  "focus returns to the confirm once a %s answer settles",
  (outcome) => {
    // The confirm is disabled for the duration of the delete, which a browser
    // blurs (jsdom does not — hence the explicit blur). AC29 wants the control
    // the operator pressed still focused when the answer arrives, so a retry
    // needs no walk back through the dialog.
    const { rerender } = render(
      <BranchDeleteConfirmModal
        open
        branch={entry()}
        isDeleting={false}
        onClose={jest.fn()}
        onConfirm={jest.fn()}
      />,
    );
    const submit = screen.getByRole("button", { name: /delete/i });
    submit.focus();
    fireEvent.click(submit);

    rerender(
      <BranchDeleteConfirmModal
        open
        branch={entry()}
        isDeleting
        onClose={jest.fn()}
        onConfirm={jest.fn()}
      />,
    );
    blurAsABrowserWould();
    expect(document.activeElement).toBe(document.body);

    rerender(
      <BranchDeleteConfirmModal
        open
        branch={entry()}
        isDeleting={false}
        outcome={outcome as "refused" | "parked"}
        refusal={{
          reason: outcome === "parked" ? "cleanup_failed" : "no_receipt",
          detail: "Nothing was deleted.",
          parked_path: outcome === "parked" ? PARKED : "",
        }}
        onClose={jest.fn()}
        onConfirm={jest.fn()}
      />,
    );

    expect(document.activeElement).toBe(submit);
  },
);

test("commits that exist nowhere else are discarded only after the person ticks the box naming them", () => {
  const { onConfirm } = renderModal({ branch: entry({ commits_only_here: 2 }) });

  const submit = screen.getByRole("button", { name: "Delete branch" });
  expect(submit).toBeDisabled();

  fireEvent.click(screen.getByRole("checkbox", { name: "Discard 2 commits with the branch" }));
  expect(submit).toBeEnabled();
  fireEvent.click(submit);

  expect(onConfirm).toHaveBeenCalledWith(true);
});

test("a branch whose commits all live elsewhere deletes without a discard", () => {
  const { onConfirm } = renderModal({ branch: entry({ commits_only_here: 0 }) });

  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.getByText(/deleting the branch loses none/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete branch" }));

  expect(onConfirm).toHaveBeenCalledWith(false);
});

test("an unknown count is treated as commits that would be lost", () => {
  renderModal({ branch: entry({ commits_only_here: null }) });

  expect(screen.getByRole("button", { name: "Delete branch" })).toBeDisabled();
  expect(
    screen.getByRole("checkbox", { name: "Delete anyway, discarding any commits only this branch has" }),
  ).toBeInTheDocument();
});

/**
 * jsdom keeps focus on a button that goes `disabled`; a browser blurs it, and
 * that blur is the whole reason AC29's focus clause was false on the real
 * surface. Moving focus to the body is that step, written out.
 */
function blurAsABrowserWould() {
  document.body.setAttribute("tabindex", "-1");
  document.body.focus();
}

// --- lg-external-harness-1415 / R-HEADING ---------------------------------

test("a cleanup_failed refusal heading drops the nothing-was-deleted tail", () => {
  renderModal({
    outcome: "refused",
    refusal: {
      reason: "cleanup_failed",
      detail: RESTORED_DETAIL,
      parked_path: "",
    },
  });

  const heading = refusalHeading();
  expect(heading).toBeInTheDocument();
  expect(heading!.textContent).toMatch(/refused/);
  expect(heading!.textContent?.replace(/\s+/g, " ").trim()).toBe("1 branch(es) refused");
  expect(heading!.textContent).not.toMatch(/nothing was deleted/i);
  expect(refusalBlock()?.textContent).toContain(RESTORED_DETAIL);
  expect(screen.queryByRole("button", { name: /^Retry$/i })).not.toBeInTheDocument();
  // AC9: Confirm is not a toast site. BranchTriageList owns that mutation's
  // toast; a second one would clamp the restored sentence.
  expect(pushToast).not.toHaveBeenCalled();
});

import { fireEvent, render, screen } from "@testing-library/react";

import { BranchDeleteConfirmModal } from "../BranchDeleteConfirmModal";
import type { BranchTriageEntry } from "../../lib/branchTriageApi";

/**
 * lg-external-harness-1026 — the single-branch confirm dialog, once the gate
 * can refuse the delete it is confirming.
 *
 * This dialog used to take only an `error` string, so a refusal arrived
 * looking exactly like a transport failure: red, and silent about what to do
 * next. A refusal means the gate worked and nothing was lost, which is a
 * different thing to tell someone.
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

function branchWith(
  linked: BranchTriageEntry["linked_tickets"] = [],
): BranchTriageEntry {
  return {
    name: "feature/x",
    is_current: false,
    is_base: false,
    squash_merged: false,
    ahead: 2,
    behind: 0,
    dirty: false,
    upstream: null,
    diff_options: [],
    worktrees: [
      { path: "/repo/.worktrees/tree-one", label: "tree-one", dirty: false, is_primary: false },
    ],
    linked_tickets: linked,
    last_commit: { date: new Date().toISOString(), message: "work" },
    issues: [],
    pr: null,
  };
}

function renderModal(props: Record<string, unknown> = {}) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  render(
    <BranchDeleteConfirmModal
      open
      branch={branchWith()}
      isDeleting={false}
      onClose={onClose}
      onConfirm={onConfirm}
      {...props}
    />,
  );
  return { onClose, onConfirm };
}

function refusalBlock(): HTMLElement | null {
  return document.querySelector(".branch-cleanup-refusals");
}

beforeEach(() => {
  mockNavigate.mockReset();
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
      branch={branchWith([
        { id: "uuid-lg-demo-3", external_id: "lg-demo-3", title: "t", state: "in_progress" },
      ])}
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
        branch={branchWith()}
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
        branch={branchWith()}
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
        branch={branchWith()}
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

/**
 * jsdom keeps focus on a button that goes `disabled`; a browser blurs it, and
 * that blur is the whole reason AC29's focus clause was false on the real
 * surface. Moving focus to the body is that step, written out.
 */
function blurAsABrowserWould() {
  document.body.setAttribute("tabindex", "-1");
  document.body.focus();
}

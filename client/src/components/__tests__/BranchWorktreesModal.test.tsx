import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BranchWorktreesModal } from "../BranchWorktreesModal";
import { removeBranchWorktree, type BranchTriageEntry } from "../../lib/branchTriageApi";
import { pushToast } from "../../state/toastStore";

/**
 * lg-external-harness-1026 — the single-worktree delete, once the gate can
 * refuse it.
 *
 * The operator's question here is the same one the bulk modal answers, asked
 * about one checkout: *can I delete this, and if not, what do I do first?*
 * Reporting only that the delete failed answers half of it.
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

jest.mock("../../lib/branchTriageApi", () => ({
  ...jest.requireActual("../../lib/branchTriageApi"),
  removeBranchWorktree: jest.fn(),
}));

const mockRemove = removeBranchWorktree as jest.MockedFunction<typeof removeBranchWorktree>;

const PATH_ONE = "/repo/.worktrees/tree-one";
const PATH_TWO = "/repo/.worktrees/tree-two";
const PARKED = "/repo/.worktrees-releasing/3f1c8a92";
/** Live-max checkout path, 2026-10-24. Interpolated into 1414's RESTORED sentence. */
const RESTORED_PATH =
  "/Users/jacobbrandt/workspace/.worktrees/ticket-loregarden-blob-procedural-sdf-4-8e77a3e8";
const WRITE_A_RECEIPT =
  "Land the branch or publish the tree — those are the two things that write the receipt this delete needs.";

function restoredSentence(path: string) {
  return (
    `The directory is still at ${path} and is still owned, but part of it may be gone ` +
    `and git no longer lists it as a worktree — delete it again, or remove the directory by hand.`
  );
}

function branchWith(
  paths: string[] = [PATH_ONE],
  linked: BranchTriageEntry["linked_tickets"] = [],
): BranchTriageEntry {
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
    worktrees: paths.map((path) => ({ path, label: path, dirty: false, is_primary: false })),
    linked_tickets: linked,
    last_commit: { date: new Date().toISOString(), message: "work" },
    issues: [],
    pr: null,
  };
}

function removed(path: string) {
  return {
    branch: "feature/x",
    removed_path: path,
    outcome: "deleted" as const,
    reason: "",
    detail: "",
    parked_path: "",
  };
}

function refused(path: string, reason = "no_receipt") {
  return {
    branch: "feature/x",
    removed_path: path,
    outcome: "refused" as const,
    reason,
    detail: "Nothing was deleted. Land the branch or publish the tree first.",
    parked_path: "",
  };
}

function parkedResult(path: string) {
  return {
    branch: "feature/x",
    removed_path: path,
    outcome: "parked" as const,
    reason: "cleanup_failed",
    detail: `Nothing was deleted. The checkout is parked at ${PARKED} and still owned.`,
    parked_path: PARKED,
  };
}

function restoredResult(path: string) {
  return {
    branch: "feature/x",
    removed_path: path,
    outcome: "refused" as const,
    reason: "cleanup_failed",
    detail: restoredSentence(path),
    parked_path: "",
  };
}

function renderModal(branch: BranchTriageEntry) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = jest.fn();
  render(
    <QueryClientProvider client={qc}>
      <BranchWorktreesModal open branch={branch} workspaceSlug="demo" onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

function deleteRow(index = 0) {
  fireEvent.click(screen.getAllByRole("button", { name: /^Delete/ })[index]);
}

function refusalBlock(): HTMLElement | null {
  return document.querySelector(".branch-cleanup-refusals");
}

function refusalHeading(): HTMLElement | null {
  return document.querySelector(".branch-cleanup-refusals .modal-section-title");
}

beforeEach(() => {
  mockRemove.mockReset();
  mockNavigate.mockReset();
  (pushToast as jest.Mock).mockReset();
  mockRemove.mockResolvedValue(removed(PATH_ONE));
  jest.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  jest.restoreAllMocks();
});

// --- AC22 / AC25 ----------------------------------------------------------

test("a refused removal renders amber and leaves the worktree in the list", async () => {
  mockRemove.mockResolvedValue(refused(PATH_ONE));
  renderModal(branchWith());

  deleteRow();

  await waitFor(() => expect(refusalBlock()).toBeInTheDocument());
  expect(document.querySelector(".branch-triage-delete-error")).not.toBeInTheDocument();
  // AC27: the list's own empty state must not be pressed into reporting this.
  // A refused removal leaves the worktree in the list, which is the point.
  //
  // Asserted on the row rather than on a unique `getByText`: AC23 requires the
  // refusal to render that same path in its own selectable `<code>` (pinned
  // below), so the path is deliberately on screen twice and a uniqueness query
  // would be asserting the opposite of the criterion next to it.
  expect(document.querySelectorAll(".branch-worktrees-row")).toHaveLength(1);
  expect(screen.getAllByText(PATH_ONE).length).toBeGreaterThan(0);
  expect(
    screen.queryByText("No worktrees are checked out for this branch."),
  ).not.toBeInTheDocument();
});

test("a transport failure keeps the red error block", async () => {
  mockRemove.mockRejectedValue(new Error("Network request failed"));
  renderModal(branchWith());

  deleteRow();

  await waitFor(() =>
    expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument(),
  );
  expect(refusalBlock()).not.toBeInTheDocument();
});

test("a parked outcome is red, names the path in a code element, and raises a toast", async () => {
  mockRemove.mockResolvedValue(parkedResult(PATH_ONE));
  renderModal(branchWith());

  deleteRow();

  await waitFor(() =>
    expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument(),
  );
  expect([...document.querySelectorAll("code")].map((n) => n.textContent)).toContain(PARKED);
  expect(JSON.stringify((pushToast as jest.Mock).mock.calls)).toContain(PARKED);
});

// --- AC23 -----------------------------------------------------------------

test("a refusal ends in a named control when the branch has a linked ticket", async () => {
  mockRemove.mockResolvedValue(refused(PATH_ONE));
  renderModal(
    branchWith(
      [PATH_ONE],
      [{ id: "uuid-lg-demo-9", external_id: "lg-demo-9", title: "t", state: "in_progress" }],
    ),
  );

  deleteRow();

  const open = await screen.findByRole("button", { name: "Open lg-demo-9" });
  fireEvent.click(open);
  expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("uuid-lg-demo-9"));
});

test("with no linked ticket the copy names landing and publishing, and the path is selectable", async () => {
  mockRemove.mockResolvedValue(refused(PATH_ONE));
  renderModal(branchWith());

  deleteRow();

  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });
  expect(block.textContent?.toLowerCase()).toContain("land");
  expect(block.textContent?.toLowerCase()).toContain("publish");
  expect([...block.querySelectorAll("code")].map((n) => n.textContent)).toContain(PATH_ONE);
});

// --- AC28 -----------------------------------------------------------------

test("every row's Delete is disabled while any one removal is pending", async () => {
  const pending: { settle?: (value: unknown) => void } = {};
  mockRemove.mockImplementation(
    () => new Promise((resolve) => { pending.settle = resolve; }) as never,
  );
  renderModal(branchWith([PATH_ONE, PATH_TWO]));

  deleteRow(0);

  await waitFor(() => {
    for (const button of screen.getAllByRole("button", { name: /^(Delete|Deleting)/ })) {
      expect(button).toBeDisabled();
    }
  });
  pending.settle?.(removed(PATH_ONE));
});

// --- AC29 / AC30 ----------------------------------------------------------

test("the refusal is announced without stealing focus and does not animate in", async () => {
  mockRemove.mockResolvedValue(refused(PATH_ONE));
  renderModal(branchWith());
  const button = screen.getAllByRole("button", { name: /^Delete/ })[0];
  button.focus();

  fireEvent.click(button);

  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });
  expect(block).toHaveAttribute("role", "status");
  expect(block).toHaveAttribute("aria-live", "polite");
  expect(document.activeElement).toBe(button);
  expect(block.style.animation).toBe("");
});

// --- AC27 -----------------------------------------------------------------

test("a successful removal shows no refusal block", async () => {
  renderModal(branchWith());

  deleteRow();

  await waitFor(() => expect(mockRemove).toHaveBeenCalled());
  expect(refusalBlock()).not.toBeInTheDocument();
});

test.each([
  ["refused", refused(PATH_ONE)],
  ["parked", parkedResult(PATH_ONE)],
])("focus returns to the pressed row Delete after a %s removal", async (_outcome, answer) => {
  // Every row's Delete is disabled while any removal is pending (AC28), so a
  // browser blurs the one that was pressed. jsdom does not, so the blur here
  // is that step; what is asserted is that focus comes back to the same
  // control rather than landing on the document body.
  const pending: { settle?: (value: unknown) => void } = {};
  mockRemove.mockImplementation(
    () => new Promise((resolve) => { pending.settle = resolve; }) as never,
  );
  renderModal(branchWith([PATH_ONE, PATH_TWO]));
  const row = screen.getAllByRole("button", { name: /^Delete/ })[0];
  row.focus();

  fireEvent.click(row);
  await waitFor(() => expect(row).toBeDisabled());
  blurAsABrowserWould();
  expect(document.activeElement).toBe(document.body);

  pending.settle?.(answer);

  await waitFor(() => expect(row).toBeEnabled());
  expect(document.activeElement).toBe(row);
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

const linkedTicket: BranchTriageEntry["linked_tickets"] = [
  { id: "uuid-lg-demo-9", external_id: "lg-demo-9", title: "t", state: "in_progress" },
];

// --- lg-external-harness-1415 / R-TOAST, R-ROW, R-HEADING -----------------

test("a restored cleanup_failed 200 is amber, does not toast, and the heading drops the tail", async () => {
  expect(RESTORED_PATH).toHaveLength(88);
  mockRemove.mockResolvedValue(restoredResult(RESTORED_PATH));
  renderModal(branchWith([RESTORED_PATH], linkedTicket));

  deleteRow();

  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });
  expect(document.querySelector(".branch-triage-delete-error")).not.toBeInTheDocument();
  expect(block).toHaveAttribute("role", "status");
  expect(block).toHaveAttribute("aria-live", "polite");
  expect(pushToast).not.toHaveBeenCalled();
  expect(block.querySelector(".branch-cleanup-refusal-parked-label")).not.toBeInTheDocument();
  const heading = refusalHeading();
  expect(heading).toBeInTheDocument();
  expect(heading!.textContent?.replace(/\s+/g, " ").trim()).toBe("1 worktree(s) refused");
  expect(heading!.textContent).not.toMatch(/nothing was deleted/i);
  expect(block.textContent).toContain(restoredSentence(RESTORED_PATH));
  expect(screen.queryByRole("button", { name: "Open lg-demo-9" })).not.toBeInTheDocument();
  expect(block.textContent).not.toContain(WRITE_A_RECEIPT);
  expect(block.textContent).not.toContain("Land the branch");
  expect(block.textContent).not.toContain("publish the tree");
  expect(screen.queryByRole("button", { name: /^Retry$/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Delete$/ })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /Deleting/ })).not.toBeInTheDocument();
});

test("a parked cleanup_failed 200 with a linked ticket does not end in Open <ticket>", async () => {
  mockRemove.mockResolvedValue(parkedResult(PATH_ONE));
  renderModal(branchWith([PATH_ONE], linkedTicket));

  deleteRow();

  await waitFor(() =>
    expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "Open lg-demo-9" })).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain(WRITE_A_RECEIPT);
});

test("a restored cleanup_failed 200 with no linked ticket does not end in WRITE_A_RECEIPT", async () => {
  // Kill (d) no-ticket half: designer pinned Open-absent with a ticket. HEAD
  // always appends WRITE_A_RECEIPT when ticket is null, which is the false
  // receipt-gate ending AC8 removes.
  mockRemove.mockResolvedValue(restoredResult(RESTORED_PATH));
  renderModal(branchWith([RESTORED_PATH]));

  deleteRow();

  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });
  expect(block.textContent).toContain(restoredSentence(RESTORED_PATH));
  expect(block.textContent).not.toContain(WRITE_A_RECEIPT);
  expect(screen.queryByRole("button", { name: /^Open / })).not.toBeInTheDocument();
  const details = [...block.querySelectorAll(".branch-cleanup-refusal-detail")].map(
    (node) => node.textContent,
  );
  expect(details.filter((text) => text === WRITE_A_RECEIPT)).toHaveLength(0);
});

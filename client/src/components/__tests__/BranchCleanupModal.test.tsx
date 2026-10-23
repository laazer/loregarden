import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BranchCleanupModal } from "../BranchCleanupModal";
import { deleteBranchTriage, type BranchTriageEntry } from "../../lib/branchTriageApi";
import { pushToast } from "../../state/toastStore";

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
  deleteBranchTriage: jest.fn(),
}));

const mockDelete = deleteBranchTriage as jest.MockedFunction<typeof deleteBranchTriage>;

function entry(overrides: Partial<BranchTriageEntry> = {}): BranchTriageEntry {
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
    worktrees: [],
    linked_tickets: [],
    last_commit: { date: new Date().toISOString(), message: "work" },
    issues: [],
    pr: null,
    ...overrides,
  };
}

function renderModal(branches: BranchTriageEntry[], onClose = jest.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BranchCleanupModal
        open
        workspaceSlug="demo"
        baseBranch="main"
        branches={branches}
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return { onClose };
}

function checkboxFor(name: string): HTMLInputElement {
  const label = screen.getByText(name).closest("label");
  const id = label?.getAttribute("for");
  return document.getElementById(id ?? "") as HTMLInputElement;
}

beforeEach(() => {
  mockDelete.mockReset();
  mockNavigate.mockReset();
  (pushToast as jest.Mock).mockReset();
  // lg-external-harness-1026: the endpoint now answers with a typed outcome,
  // and the client branches on it rather than on "the request did not throw".
  mockDelete.mockResolvedValue({
    deleted: "feature/x",
    already_gone: false,
    removed_worktrees: true,
    outcome: "deleted",
    reason: "",
    detail: "",
    parked_path: "",
  });
});

test("offers no branch the repo would refuse to delete", () => {
  renderModal([
    entry({ name: "main", is_base: true, ahead: 0 }),
    entry({ name: "feature/current", is_current: true, ahead: 0 }),
    entry({ name: "feature/merged", ahead: 0 }),
  ]);

  expect(screen.queryByText("main")).not.toBeInTheDocument();
  expect(screen.queryByText("feature/current")).not.toBeInTheDocument();
  expect(screen.getByText("feature/merged")).toBeInTheDocument();
});

test("pre-selects merged branches and leaves unmerged and dirty ones alone", () => {
  renderModal([
    entry({ name: "feature/merged-pr", pr: { state: "merged", is_draft: false, url: "u", number: 1, title: "t" } }),
    entry({ name: "feature/squashed", ahead: 0, squash_merged: true }),
    entry({ name: "feature/live", ahead: 3 }),
    entry({ name: "feature/dirty", ahead: 0, dirty: true }),
  ]);

  expect(checkboxFor("feature/merged-pr").checked).toBe(true);
  expect(checkboxFor("feature/squashed").checked).toBe(true);
  expect(checkboxFor("feature/live").checked).toBe(false);
  // Merged, but holds uncommitted work — never ticked on the operator's behalf.
  expect(checkboxFor("feature/dirty").checked).toBe(false);
  expect(screen.getByRole("button", { name: "Delete selected (2)" })).toBeEnabled();
});

test("a category checkbox selects and clears exactly its members", () => {
  renderModal([
    entry({ name: "feature/stale", ahead: 4, issues: [{ code: "stale", severity: "low", message: "old" }] }),
    entry({ name: "feature/live", ahead: 3 }),
  ]);

  expect(checkboxFor("feature/stale").checked).toBe(false);

  fireEvent.click(screen.getByLabelText(/Stale/));
  expect(checkboxFor("feature/stale").checked).toBe(true);
  expect(checkboxFor("feature/live").checked).toBe(false);

  fireEvent.click(screen.getByLabelText(/Stale/));
  expect(checkboxFor("feature/stale").checked).toBe(false);
});

test("deletes each selected branch, removing worktrees, and closes when all succeed", async () => {
  const { onClose } = renderModal([
    entry({ name: "feature/a", ahead: 0 }),
    entry({
      name: "feature/b",
      ahead: 0,
      worktrees: [{ path: "/wt/b", label: "b", dirty: false, is_primary: false }],
    }),
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Delete selected (2)" }));

  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(mockDelete).toHaveBeenCalledWith("demo", "feature/a", true, false);
  expect(mockDelete).toHaveBeenCalledWith("demo", "feature/b", true, true);
});

test("one branch failing does not stop the rest, and the failure is named", async () => {
  mockDelete.mockImplementation(async (_slug, branch) => {
    if (branch === "feature/a") throw new Error("worktree is locked");
    // lg-external-harness-1026: the outcome is now a required field, so a
    // deletion has to say it is one. The assertion below is unchanged.
    return {
      deleted: branch,
      already_gone: false,
      removed_worktrees: true,
      outcome: "deleted" as const,
      reason: "",
      detail: "",
      parked_path: "",
    };
  });

  const { onClose } = renderModal([
    entry({ name: "feature/a", ahead: 0 }),
    entry({ name: "feature/b", ahead: 0 }),
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Delete selected (2)" }));

  expect(await screen.findByText(/worktree is locked/)).toBeInTheDocument();
  expect(mockDelete).toHaveBeenCalledWith("demo", "feature/b", true, false);
  // The operator still has a decision to make about the survivor, so the
  // dialog stays up rather than closing over its own error.
  expect(onClose).not.toHaveBeenCalled();
});

test("says so plainly when there is nothing it can clean up", () => {
  renderModal([entry({ name: "main", is_base: true, ahead: 0 })]);

  expect(screen.getByText(/Nothing to clean up/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete selected (0)" })).toBeDisabled();
});

// ===========================================================================
// lg-external-harness-1026 — the receipt gate at the screen.
//
// Before this, `deleted[]` accumulated a branch whenever the request did not
// throw, so a 200 whose body said "refused" was reported to the operator as a
// deletion and the modal closed on it. That is AC13 ("every refusal says what
// it did not delete") being true in a server log and false everywhere a person
// can see.
//
// The question these pin, from the operator's side: *which of these branches
// can I delete right now, and for the ones the gate refused, what do I have to
// do first?* A refusal that only reports a failure answers the first half.
// ===========================================================================

function refused(
  branch: string,
  reason = "no_receipt",
  detail = "Nothing was deleted. Land the branch or publish the tree first.",
) {
  return {
    deleted: branch,
    already_gone: false,
    removed_worktrees: false,
    outcome: "refused" as const,
    reason,
    detail,
    parked_path: "",
  };
}

function deletedOk(branch: string) {
  return {
    deleted: branch,
    already_gone: false,
    removed_worktrees: true,
    outcome: "deleted" as const,
    reason: "",
    detail: "",
    parked_path: "",
  };
}

function parked(branch: string, path: string) {
  return {
    deleted: branch,
    already_gone: false,
    removed_worktrees: false,
    outcome: "parked" as const,
    reason: "cleanup_failed",
    detail: `Nothing was deleted. The checkout is parked at ${path} and still owned.`,
    parked_path: path,
  };
}

const ticket = (external_id: string) => ({
  id: `uuid-${external_id}`,
  external_id,
  title: "Add the thing",
  state: "in_progress",
});

function refusalBlock(): HTMLElement | null {
  return document.querySelector(".branch-cleanup-refusals");
}

async function deleteSelected() {
  fireEvent.click(screen.getByRole("button", { name: /Delete selected/ }));
  await waitFor(() => expect(mockDelete).toHaveBeenCalled());
}

// --- AC22 -----------------------------------------------------------------

test("a 200 refusal is not counted as a deletion", async () => {
  mockDelete.mockResolvedValue(refused("feature/kept"));
  const onBranchesDeleted = jest.fn();
  const onClose = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BranchCleanupModal
        open
        workspaceSlug="demo"
        baseBranch="main"
        branches={[entry({ name: "feature/kept", ahead: 0 })]}
        onClose={onClose}
        onBranchesDeleted={onBranchesDeleted}
      />
    </QueryClientProvider>,
  );

  await deleteSelected();

  await waitFor(() => expect(refusalBlock()).toBeInTheDocument());
  expect(onBranchesDeleted).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test("a mixed run reports only the branches that were actually deleted", async () => {
  mockDelete.mockImplementation(async (_slug, branch) =>
    branch === "feature/gone" ? deletedOk(branch) : refused(branch),
  );
  const onBranchesDeleted = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BranchCleanupModal
        open
        workspaceSlug="demo"
        baseBranch="main"
        branches={[
          entry({ name: "feature/gone", ahead: 0 }),
          entry({ name: "feature/kept", ahead: 0 }),
        ]}
        onClose={jest.fn()}
        onBranchesDeleted={onBranchesDeleted}
      />
    </QueryClientProvider>,
  );

  await deleteSelected();

  await waitFor(() => expect(onBranchesDeleted).toHaveBeenCalledWith(["feature/gone"]));
});

// --- AC25 -----------------------------------------------------------------

test("a refusal renders amber, apart from the red error block", async () => {
  mockDelete.mockResolvedValue(refused("feature/kept"));
  renderModal([entry({ name: "feature/kept", ahead: 0 })]);

  await deleteSelected();

  await waitFor(() => expect(refusalBlock()).toBeInTheDocument());
  // The gate worked and nothing was lost. Styling that as a failure is how an
  // operator learns to force past a safety bar.
  expect(document.querySelector(".branch-triage-delete-error")).not.toBeInTheDocument();
  expect(screen.getByText(/1 branch\(es\)? refused — nothing was deleted/i)).toBeInTheDocument();
});

test("a transport failure keeps the existing red error path", async () => {
  mockDelete.mockRejectedValue(new Error("Network request failed"));
  renderModal([entry({ name: "feature/kept", ahead: 0 })]);

  await deleteSelected();

  await waitFor(() =>
    expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument(),
  );
  expect(refusalBlock()).not.toBeInTheDocument();
});

test("a parked outcome is the loud one: red, the path in a code element, and a toast", async () => {
  const path = "/repo/.worktrees-releasing/9f2c1ab4";
  mockDelete.mockResolvedValue(parked("feature/stuck", path));
  renderModal([entry({ name: "feature/stuck", ahead: 0 })]);

  await deleteSelected();

  await waitFor(() =>
    expect(document.querySelector(".branch-triage-delete-error")).toBeInTheDocument(),
  );
  const code = [...document.querySelectorAll("code")].map((node) => node.textContent);
  expect(code).toContain(path);
  // The path is the whole recovery procedure, and the modal is about to close.
  expect(pushToast).toHaveBeenCalled();
  expect(JSON.stringify((pushToast as jest.Mock).mock.calls)).toContain(path);
});

// --- AC23 -----------------------------------------------------------------

test("a refused branch with a linked ticket ends in a button, not in prose", async () => {
  mockDelete.mockResolvedValue(refused("feature/kept"));
  renderModal([entry({ name: "feature/kept", ahead: 0, linked_tickets: [ticket("lg-demo-7")] })]);

  await deleteSelected();

  const open = await screen.findByRole("button", { name: "Open lg-demo-7" });
  fireEvent.click(open);
  // That ticket is where the operator lands the branch or publishes the tree —
  // the two verbs that write the receipt this refusal is missing.
  expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("uuid-lg-demo-7"));
});

test("a refused branch with no linked ticket names the two verbs instead", async () => {
  mockDelete.mockResolvedValue(refused("feature/orphan"));
  renderModal([entry({ name: "feature/orphan", ahead: 0, linked_tickets: [] })]);

  await deleteSelected();

  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });
  expect(block.textContent?.toLowerCase()).toContain("land");
  expect(block.textContent?.toLowerCase()).toContain("publish");
});

// --- AC24 -----------------------------------------------------------------

test("refusals are grouped by the reason key the server sent, with a count each", async () => {
  mockDelete.mockImplementation(async (_slug, branch) =>
    refused(branch, branch.endsWith("0") ? "uncommitted" : "no_receipt"),
  );
  renderModal(
    Array.from({ length: 4 }, (_unused, index) => entry({ name: `feature/b${index}`, ahead: 0 })),
  );

  await deleteSelected();

  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });
  // Grouped on the key, never on the rendered sentence: a grouping keyed on
  // prose breaks the first time anyone edits a message.
  expect(block.textContent).toMatch(/3/);
  expect(block.textContent).toMatch(/1/);
});

test("a double-digit run shows ten rows and hides the rest behind a disclosure", async () => {
  mockDelete.mockImplementation(async (_slug, branch) => refused(branch));
  renderModal(
    Array.from({ length: 13 }, (_unused, index) => entry({ name: `feature/b${index}`, ahead: 0 })),
  );

  await deleteSelected();

  const disclosure = await screen.findByRole("button", { name: /Show all 13/ });
  expect(disclosure).toHaveAttribute("aria-expanded", "false");
  expect(refusalBlock()!.querySelectorAll("li")).toHaveLength(10);

  fireEvent.click(disclosure);

  expect(disclosure).toHaveAttribute("aria-expanded", "true");
  expect(refusalBlock()!.querySelectorAll("li")).toHaveLength(13);
});

// --- AC21 -----------------------------------------------------------------
//
// Measured on the sandbox at fffd661a with 110 branches selected and all 110
// refused: `.branch-cleanup-refusals` had a viewport top of 13223px in an
// 1100px viewport, 13,035px below the modal body's scroll position and behind
// 214 branch rows. Nothing above the fold said the gate refused anything, so
// the screen answered "nothing happened" to a question the operator had just
// asked. The block is information with a decision attached; it goes first.

test("the gate's answer is above the branch list, not behind it", async () => {
  mockDelete.mockImplementation(async (_slug, branch) => refused(branch));
  renderModal(
    Array.from({ length: 13 }, (_unused, index) => entry({ name: `feature/b${index}`, ahead: 0 })),
  );

  await deleteSelected();
  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });

  const categories = document.querySelector(".branch-cleanup-categories")!;
  const branchList = document.querySelector(".branch-cleanup-branches")!;
  // DOCUMENT_POSITION_FOLLOWING: the compared node comes after `block`.
  expect(block.compareDocumentPosition(categories) & Node.DOCUMENT_POSITION_FOLLOWING)
    .toBeTruthy();
  expect(block.compareDocumentPosition(branchList) & Node.DOCUMENT_POSITION_FOLLOWING)
    .toBeTruthy();
});

test("a parked or failed block is above the branch list too", async () => {
  mockDelete.mockImplementation(async (_slug, branch) =>
    parked(branch, `/tmp/.worktrees-releasing/${branch.replace("/", "-")}`),
  );
  renderModal([entry({ name: "feature/b0", ahead: 0 }), entry({ name: "feature/b1", ahead: 0 })]);

  await deleteSelected();
  const block = await waitFor(() => {
    const found = document.querySelector(".branch-cleanup-failures");
    expect(found).toBeInTheDocument();
    return found!;
  });

  const categories = document.querySelector(".branch-cleanup-categories")!;
  expect(block.compareDocumentPosition(categories) & Node.DOCUMENT_POSITION_FOLLOWING)
    .toBeTruthy();
});

test("the footer keeps the refused count once the run is at rest", async () => {
  mockDelete.mockImplementation(async (_slug, branch) => refused(branch));
  renderModal(
    Array.from({ length: 3 }, (_unused, index) => entry({ name: `feature/b${index}`, ahead: 0 })),
  );

  await deleteSelected();
  await waitFor(() => expect(refusalBlock()).toBeInTheDocument());

  // At rest the status reverts to the selection summary. Without the refused
  // count it reads "3 selected" — the same words it showed before the run.
  const status = document.querySelector(".branch-cleanup-status")!;
  expect(status.textContent).not.toMatch(/Deleting/);
  expect(status.textContent).toMatch(/3 selected/);
  expect(status.textContent).toMatch(/3 refused/);
});

// --- AC26 -----------------------------------------------------------------

test("the footer status accrues refusals while the run is still going", async () => {
  const pending: { settle?: (value: unknown) => void } = {};
  mockDelete.mockImplementationOnce(async (_slug, branch) => refused(branch));
  mockDelete.mockImplementationOnce(
    () => new Promise((resolve) => { pending.settle = resolve; }) as never,
  );
  renderModal([
    entry({ name: "feature/b0", ahead: 0 }),
    entry({ name: "feature/b1", ahead: 0 }),
  ]);

  fireEvent.click(screen.getByRole("button", { name: /Delete selected/ }));

  const status = await waitFor(() => {
    const node = document.querySelector(".branch-cleanup-status")!;
    expect(node.textContent).toMatch(/1 refused/);
    return node;
  });
  expect(status).toHaveAttribute("aria-live", "polite");
  expect(status.textContent).toMatch(/Deleting \d+ of 2/);
  pending.settle?.(deletedOk("feature/b1"));
});

// --- AC27 -----------------------------------------------------------------

test("zero refusals renders no block at all and closes as it does today", async () => {
  mockDelete.mockImplementation(async (_slug, branch) => deletedOk(branch));
  const { onClose } = renderModal([entry({ name: "feature/gone", ahead: 0 })]);

  await deleteSelected();

  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(refusalBlock()).not.toBeInTheDocument();
  expect(screen.queryByText(/no refusals/i)).not.toBeInTheDocument();
});

// --- AC28 -----------------------------------------------------------------

test("a second click cannot fire a second rename-then-delete", async () => {
  const pending: { settle?: (value: unknown) => void } = {};
  mockDelete.mockImplementation(
    () => new Promise((resolve) => { pending.settle = resolve; }) as never,
  );
  renderModal([entry({ name: "feature/b0", ahead: 0 })]);
  const submit = screen.getByRole("button", { name: /Delete selected/ });

  fireEvent.click(submit);
  await waitFor(() => expect(submit).toBeDisabled());
  fireEvent.click(submit);

  expect(mockDelete).toHaveBeenCalledTimes(1);
  pending.settle?.(deletedOk("feature/b0"));
});

test("Escape and the backdrop stay inert while a delete is in flight", async () => {
  // AC28 says this behaviour "must survive this change", and nothing asserted
  // it. The release it guards is a rename-then-delete: dismissing the modal
  // mid-run does not cancel the requests already issued, so the operator would
  // be left with branches disappearing behind a closed dialog and the refusal
  // block — the only place the gate's answer is shown — never rendered.
  const pending: { settle?: (value: unknown) => void } = {};
  mockDelete.mockImplementation(
    () => new Promise((resolve) => { pending.settle = resolve; }) as never,
  );
  const { onClose } = renderModal([entry({ name: "feature/inflight", ahead: 0 })]);
  const submit = screen.getByRole("button", { name: /Delete selected/ });

  fireEvent.click(submit);
  await waitFor(() => expect(submit).toBeDisabled());

  fireEvent.keyDown(document, { key: "Escape" });
  const backdrop = document.querySelector(".modal-overlay");
  expect(backdrop).not.toBeNull();
  fireEvent.click(backdrop!);

  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeInTheDocument();

  pending.settle?.(deletedOk("feature/inflight"));
  // And once it has settled, the dialog is dismissible again — inert while
  // busy, not inert for good.
  await waitFor(() => expect(onClose).toHaveBeenCalled());
});

test("a refusal mid-run does not unlock dismissal before the rest have answered", async () => {
  // The refusal arrives while later branches are still in flight. A `busy`
  // flag cleared on the first *answer* rather than on the last would reopen
  // Escape here, and Escape unmounts the block the refusal was just written
  // into.
  const held: { settle?: (value: unknown) => void } = {};
  mockDelete.mockImplementation(async (_slug, branch) =>
    branch === "feature/first"
      ? refused(branch)
      : ((new Promise((resolve) => { held.settle = resolve; })) as never),
  );
  const { onClose } = renderModal([
    entry({ name: "feature/first", ahead: 0 }),
    entry({ name: "feature/second", ahead: 0 }),
  ]);

  await deleteSelected();
  await waitFor(() => expect(mockDelete).toHaveBeenCalledTimes(2));

  fireEvent.keyDown(document, { key: "Escape" });

  expect(onClose).not.toHaveBeenCalled();
  held.settle?.(deletedOk("feature/second"));
});

// --- AC29 / AC30 ----------------------------------------------------------

test("the refusal block is announced without stealing focus and without animating in", async () => {
  mockDelete.mockResolvedValue(refused("feature/kept"));
  renderModal([entry({ name: "feature/kept", ahead: 0 })]);
  const submit = screen.getByRole("button", { name: /Delete selected/ });
  submit.focus();

  fireEvent.click(submit);
  const block = await waitFor(() => {
    const found = refusalBlock();
    expect(found).toBeInTheDocument();
    return found!;
  });

  expect(block).toHaveAttribute("role", "status");
  expect(block).toHaveAttribute("aria-live", "polite");
  // Focus stays where the operator put it, so a retry needs no re-navigation.
  expect(document.activeElement).toBe(submit);
  // The modal is already on screen; this is information wanted now, not a
  // thing to watch arrive.
  expect(block.style.animation).toBe("");
  expect(block.className).not.toMatch(/enter|fade|slide/i);
});

test.each([
  ["refused", refused("feature/kept")],
  ["parked", parked("feature/kept", "/repo/.worktrees-releasing/9ab1")],
])("focus returns to the submit after a %s run, not to the document body", async (_outcome, answer) => {
  // jsdom does not blur a focused element when it goes `disabled`; a browser
  // does, and on the real surface that left `document.activeElement` on BODY
  // once the gate's answer landed — so the next Tab reached the close X and
  // then ~250 branch rows before the operator could retry. The explicit blur
  // below IS that browser step; the assertion afterwards is about what puts
  // focus back.
  const pending: { settle?: (value: unknown) => void } = {};
  mockDelete.mockImplementation(
    () => new Promise((resolve) => { pending.settle = resolve; }) as never,
  );
  renderModal([entry({ name: "feature/kept", ahead: 0 })]);
  const submit = screen.getByRole("button", { name: /Delete selected/ });
  submit.focus();

  fireEvent.click(submit);
  await waitFor(() => expect(submit).toBeDisabled());
  blurAsABrowserWould();
  expect(document.activeElement).toBe(document.body);

  pending.settle?.(answer);

  await waitFor(() => expect(submit).toBeEnabled());
  expect(document.activeElement).toBe(submit);
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

// --- the wire, not a mock of it ------------------------------------------

test("a worktree the server keeps says why, from the server's own response", async () => {
  // Every test above mocks `deleteBranchTriage`, so none of them pins the wire
  // contract: this one goes through the real request wrapper and asserts the
  // server's own sentence reaches the dialog intact. The body is what the
  // delete endpoint sends when the gate refuses — a 200 carrying `outcome:
  // "refused"`, not a 4xx, because the gate worked and nothing was lost — and
  // `detail` is the refusal `checkout_guard` wrote for a checkout holding work
  // that exists nowhere else.
  const actual =
    jest.requireActual<typeof import("../../lib/branchTriageApi")>("../../lib/branchTriageApi");
  mockDelete.mockImplementation(actual.deleteBranchTriage);
  const refusal =
    "Nothing was deleted: It has uncommitted or untracked changes that exist nowhere else.";
  const body = {
    deleted: "feature/b",
    already_gone: false,
    removed_worktrees: false,
    outcome: "refused",
    reason: "uncommitted",
    detail: refusal,
    parked_path: "",
  };
  const realFetch = globalThis.fetch;
  // Both readers the wrapper uses: `json()` on a 2xx, `text()` on anything else.
  globalThis.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  }) as unknown as typeof fetch;

  try {
    const { onClose } = renderModal([
      entry({
        name: "feature/b",
        ahead: 0,
        worktrees: [{ path: "/wt/b", label: "b", dirty: false, is_primary: false }],
      }),
    ]);
    await deleteSelected();

    const row = await screen.findByText(
      (_unused, el) => el?.tagName === "LI" && el.textContent?.includes(refusal) === true,
    );
    // A 200 refusal is the gate working, so it lands in the amber refusal block
    // (UI AC25), never in the red block a transport failure uses (UI AC22).
    expect(row.closest(".branch-cleanup-refusals")).not.toBeNull();
    expect(row.closest(".branch-triage-delete-error")).toBeNull();
    expect(document.querySelector(".branch-triage-delete-error")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  } finally {
    globalThis.fetch = realFetch;
  }
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BranchCleanupModal } from "../BranchCleanupModal";
import { deleteBranchTriage, type BranchTriageEntry } from "../../lib/branchTriageApi";

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
  mockDelete.mockResolvedValue({ deleted: "feature/x" });
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
    return { deleted: branch };
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

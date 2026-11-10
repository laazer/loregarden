/**
 * The disk cleanup dialog's two read-only lists, and their keyboard paths.
 *
 * lg-milestone-that-2215: `.disk-cleanup-list` is a bounded `overflow: auto`
 * <ul> at three element sites, and they disagree. The "Can go" site holds a
 * checkbox per row and is already reachable, so it gets no change. The "Kept"
 * site and the outcome report are read-only — paths and reasons with no
 * control in them — so neither could be scrolled with the arrow keys, which
 * is where the reason a folder was refused actually is.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";

import { DiskCleanupModal } from "../DiskCleanupModal";
import { diskApi } from "../../../api/diskApi";
import type { DiskCleanupPlan, DiskCleanupResult, DiskVerdict } from "../../../api/diskTypes";

jest.mock("../../../api/diskApi", () => ({
  diskApi: { previewCleanup: jest.fn(), executeCleanup: jest.fn() },
}));
const mockPreview = diskApi.previewCleanup as jest.MockedFunction<typeof diskApi.previewCleanup>;
const mockExecute = diskApi.executeCleanup as jest.MockedFunction<typeof diskApi.executeCleanup>;

const ALLOWED_PATH = "/repo/.worktrees/tree-one";
const REFUSED_PATH = "/repo/.worktrees/tree-two";

function verdict(path: string, allowed: boolean): DiskVerdict {
  return {
    path,
    kind: "worktree",
    allowed,
    method: allowed ? "trash" : null,
    reasons: allowed ? [] : ["uncommitted changes"],
    notes: allowed ? ["Nothing is running against it."] : ["It has uncommitted changes."],
    fingerprint: `fp-${path}`,
    worktree_repo: "/repo",
  } as DiskVerdict;
}

function plan(overrides: Partial<DiskCleanupPlan> = {}): DiskCleanupPlan {
  return {
    plan_id: "plan-1",
    expires_at: "2026-11-07T12:00:00Z",
    items: [
      { verdict: verdict(ALLOWED_PATH, true), size_bytes: 1024 },
      { verdict: verdict(REFUSED_PATH, false), size_bytes: 2048 },
    ],
    allowed_bytes: 1024,
    allowed_count: 1,
    refused_count: 1,
    covered: [],
    ...overrides,
  };
}

const KEPT = "Folders kept";
const OUTCOME = "Cleanup outcome by folder";

function renderModal() {
  const onClose = jest.fn();
  const onCleaned = jest.fn();
  render(
    <DiskCleanupModal
      open
      paths={[ALLOWED_PATH, REFUSED_PATH]}
      onClose={onClose}
      onCleaned={onCleaned}
    />,
  );
  return { onClose, onCleaned };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("the Kept list's keyboard path", () => {
  it("is a list, named, and a tab stop stepping on to Cancel — AC3", async () => {
    mockPreview.mockResolvedValue(plan());
    renderModal();

    // `getByRole("list")`, not "group": a <ul> keeps its native `list` role.
    const list = await screen.findByRole("list", { name: KEPT });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveClass("scroll-focus-ring");

    // The footer FOLLOWS the list, so Tab forward is the step that exists;
    // nothing inside the Kept rows is interactive.
    list.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("leaves the Can go list alone, because every row holds a checkbox — AC5", async () => {
    mockPreview.mockResolvedValue(plan());
    renderModal();

    // Same rule block, a different element site: that region is reachable
    // through its own checkboxes and must not gain a dead tab stop.
    const checkbox = await screen.findByRole("checkbox");
    const list = checkbox.closest("ul");
    expect(list).not.toBeNull();
    expect(list).not.toHaveAttribute("tabindex");
    expect(list).not.toHaveClass("scroll-focus-ring");
  });

  it("renders no labelled Kept list when nothing was refused — AC8", async () => {
    mockPreview.mockResolvedValue(
      plan({
        items: [{ verdict: verdict(ALLOWED_PATH, true), size_bytes: 1024 }],
        refused_count: 0,
      }),
    );
    renderModal();

    // The section is gated on there being something refused, so there is no
    // named list with nothing behind it.
    await screen.findByRole("checkbox");
    expect(screen.queryByRole("list", { name: KEPT })).toBeNull();
    expect(screen.queryByText(/^Kept \(/)).toBeNull();
  });

  it("lets Escape from inside the list still close the dialog — AC9", async () => {
    mockPreview.mockResolvedValue(plan());
    const { onClose } = renderModal();

    const list = await screen.findByRole("list", { name: KEPT });
    list.focus();
    fireEvent.keyDown(list, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("the outcome report's keyboard path", () => {
  async function runCleanup(result: DiskCleanupResult) {
    mockPreview.mockResolvedValue(plan());
    mockExecute.mockResolvedValue(result);
    const handles = renderModal();
    // Every allowed path arrives already ticked, so the confirm is reachable
    // as soon as the plan lands; unticking is the operator's opt-out.
    await screen.findByRole("checkbox");
    await userEvent.click(screen.getByRole("button", { name: /to Trash$/ }));
    await waitFor(() => expect(mockExecute).toHaveBeenCalled());
    return handles;
  }

  it("is a list, named, and a tab stop stepping on to Done — AC3", async () => {
    await runCleanup({
      results: [{ path: ALLOWED_PATH, outcome: "trashed", size_bytes: 1024, detail: "" }],
      trashed_bytes: 1024,
      trashed_count: 1,
      refused_count: 0,
      failed_count: 0,
    });

    const list = await screen.findByRole("list", { name: OUTCOME });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveClass("scroll-focus-ring");
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);

    // Once the cleanup has run the footer holds one control, `Done`, and it
    // FOLLOWS the report.
    list.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Done" })).toHaveFocus();
  });

  it("puts a line in its place when nothing was moved — AC8", async () => {
    await runCleanup({
      results: [],
      trashed_bytes: 0,
      trashed_count: 0,
      refused_count: 0,
      failed_count: 0,
    });

    expect(await screen.findByText("Nothing was moved.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: OUTCOME })).toBeNull();
  });
});

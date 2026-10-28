import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { BranchTriageEntry } from "../../lib/branchTriageApi";
import { BranchTriageList } from "../BranchTriageList";

const branch = (name: string, tickets: string[] = []): BranchTriageEntry => ({
  name,
  is_current: false,
  is_base: false,
  squash_merged: false,
  commits_only_here: 0,
  ahead: 1,
  behind: 0,
  dirty: false,
  upstream: null,
  diff_options: [],
  worktrees: [],
  linked_tickets: tickets.map((id) => ({ id: `id-${id}`, external_id: id, title: id, state: "in_progress" })),
  last_commit: { date: "2026-10-01T00:00:00Z", message: "wip" },
  issues: [],
  pr: null,
});

const BRANCHES = [
  branch("ux/memory-list-volume", ["lg-memory-12"]),
  branch("ux/mcp-rules-table"),
  branch("claude/serene-dubinsky"),
];

function renderList(branches: BranchTriageEntry[] = BRANCHES, onCleanUp?: () => void) {
  const onSelectBranch = jest.fn();
  const onReviewBranch = jest.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <BranchTriageList
          workspaceSlug="loregarden"
          branches={branches}
          selectedBranch={null}
          onSelectBranch={onSelectBranch}
          onReviewBranch={onReviewBranch}
          onCleanUp={onCleanUp}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onSelectBranch, onReviewBranch, field: screen.getByRole("searchbox", { name: "Filter branches by name or linked ticket" }) };
}

const shownBranches = () => document.querySelectorAll(".branch-triage-table tbody tr").length;

describe("BranchTriageList filter", () => {
  it("narrows the list by branch name", () => {
    const { field } = renderList();
    expect(shownBranches()).toBe(3);

    fireEvent.change(field, { target: { value: "ux/" } });
    expect(shownBranches()).toBe(2);
    expect(screen.queryByText("claude/serene-dubinsky")).not.toBeInTheDocument();
  });

  it("finds a branch by the ticket linked to it", () => {
    const { field } = renderList();
    fireEvent.change(field, { target: { value: "lg-memory-12" } });
    expect(shownBranches()).toBe(1);
    expect(screen.getByText("ux/memory-list-volume")).toBeInTheDocument();
  });

  it("says what it searched for when nothing matches, and Escape or the button clears it", () => {
    const { field } = renderList();
    fireEvent.change(field, { target: { value: "zzz" } });
    expect(shownBranches()).toBe(0);
    expect(screen.getByText(/“zzz”/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show every branch" }));
    expect(shownBranches()).toBe(3);

    fireEvent.change(field, { target: { value: "ux" } });
    fireEvent.keyDown(field, { key: "Escape" });
    expect(field).toHaveValue("");
  });
});

describe("BranchTriageList next move", () => {
  const merged = { ...branch("feat/landed"), ahead: 0, behind: 40 };
  const prMerged = { ...branch("feat/pr-merged"), pr: { state: "merged", number: 12, title: "x", url: "https://example.test/12", is_draft: false } } as BranchTriageEntry;
  const onlyHere = { ...branch("feat/landed-but-unique"), ahead: 0, commits_only_here: 2 };
  const stale = {
    ...branch("wip/old", ["lg-old-3"]),
    issues: [{ code: "stale", severity: "low" as const, message: "No commits in the last 30 days" }],
  };
  const MIX = [merged, prMerged, onlyHere, stale];

  it("counts each next move and narrows the table to one", () => {
    renderList(MIX);
    const moves = screen.getByRole("group", { name: "Next move" });
    // A branch can be merged and have no ticket, so the views overlap.
    expect(within(moves).getByRole("button", { name: "All 4" })).toHaveAttribute("aria-pressed", "true");
    expect(within(moves).getByRole("button", { name: "Merged 3" })).toBeInTheDocument();
    expect(within(moves).getByRole("button", { name: "Stale 1" })).toBeInTheDocument();
    expect(within(moves).getByRole("button", { name: "No ticket 3" })).toBeInTheDocument();

    fireEvent.click(within(moves).getByRole("button", { name: "Stale 1" }));
    expect(shownBranches()).toBe(1);
    expect(screen.getByRole("button", { name: "wip/old" })).toBeInTheDocument();
  });

  it("offers one bulk delete for the merged branches the dialog would tick, never one that holds the only copy", () => {
    const onCleanUp = jest.fn();
    renderList(MIX, onCleanUp);
    fireEvent.click(screen.getByRole("button", { name: "Delete 2 merged…" }));
    expect(onCleanUp).toHaveBeenCalled();
  });

  it("selects a branch from its name and keeps its other actions in its menu", () => {
    const { onSelectBranch, onReviewBranch } = renderList(MIX);
    fireEvent.click(screen.getByRole("button", { name: "wip/old" }));
    expect(onSelectBranch).toHaveBeenCalledWith("wip/old");

    fireEvent.click(screen.getByRole("button", { name: "wip/old actions" }));
    const menu = screen.getByRole("menu", { name: "wip/old actions" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Diff review",
      "Check out…",
      "Worktrees…",
      "Open ticket",
      "Delete…",
    ]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Diff review" }));
    expect(onReviewBranch).toHaveBeenCalledWith("wip/old");
  });
});

describe("BranchTriageList families", () => {
  const family = Array.from({ length: 14 }, (_, i) => branch(`loregarden/lg-milestone-that-${1300 + i}`));

  it("folds more than twelve branches sharing a stem into one row, and opens it on request", () => {
    renderList([...family, branch("fix/baxter-head")]);
    // One family row plus the lone branch.
    expect(shownBranches()).toBe(2);
    const toggle = screen.getByRole("button", { name: /loregarden\/lg-milestone-that-…\s*14 branches/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(shownBranches()).toBe(16);
  });

  it("does not fold while you search, so a match is always a row", () => {
    const { field } = renderList(family);
    fireEvent.change(field, { target: { value: "1305" } });
    expect(screen.getByRole("button", { name: "loregarden/lg-milestone-that-1305" })).toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
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

function renderList() {
  const onSelectBranch = jest.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <BranchTriageList
          workspaceSlug="loregarden"
          branches={BRANCHES}
          selectedBranch={null}
          onSelectBranch={onSelectBranch}
          onReviewBranch={jest.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onSelectBranch, field: screen.getByRole("searchbox", { name: "Filter branches by name or linked ticket" }) };
}

const shownBranches = () => Array.from(document.querySelectorAll(".branch-triage-card")).length;

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

    fireEvent.click(screen.getByRole("button", { name: "Clear the filter" }));
    expect(shownBranches()).toBe(3);

    fireEvent.change(field, { target: { value: "ux" } });
    fireEvent.keyDown(field, { key: "Escape" });
    expect(field).toHaveValue("");
  });
});

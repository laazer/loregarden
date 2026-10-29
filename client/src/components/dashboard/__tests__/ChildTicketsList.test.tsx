import { fireEvent, render, screen, within } from "@testing-library/react";

import type { TicketTreeNode } from "../../../api/client";
import type { TicketState } from "../../../api/types";
import { ChildTicketsList } from "../ChildTicketsList";

const node = (i: number, state: TicketState, children: TicketTreeNode[] = []): TicketTreeNode => ({
  id: `c-${i}`,
  external_id: `lg-child-${i}`,
  title: `Child ${i}`,
  state,
  priority: 3,
  work_item_type: "bug",
  workspace_slug: "loregarden",
  workflow_stage_name: "Triage",
  workflow_stage_status: "pending",
  child_count: children.length,
  children,
});

function renderList(nodes: TicketTreeNode[]) {
  const onSelect = jest.fn();
  render(
    <ChildTicketsList
      nodes={nodes}
      selectedId={null}
      expandedIds={new Set()}
      onSelect={onSelect}
      onToggle={jest.fn()}
      onAddChild={jest.fn()}
    />,
  );
  return { onSelect };
}

/** 20 children: 2 blocked, 5 in progress, 3 backlog, 10 done. */
const MANY = [
  ...[1, 2].map((i) => node(i, "blocked")),
  ...[3, 4, 5, 6, 7].map((i) => node(i, "in_progress")),
  ...[8, 9, 10].map((i) => node(i, "backlog")),
  ...Array.from({ length: 10 }, (_, k) => node(11 + k, "done")),
];

const groupFor = (label: string) => screen.getByText(label, { selector: ".child-tickets-summary span" }).closest("details")!;

describe("ChildTicketsList", () => {
  it("shows a dozen or fewer as they always were, with no filter", () => {
    renderList(MANY.slice(0, 12));
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByText("Done", { selector: ".child-tickets-summary span" })).not.toBeInTheDocument();
    expect(screen.getByText("Child 1")).toBeInTheDocument();
  });

  it("groups more than a dozen by state, needs-a-person first, with counts, settled work folded", () => {
    renderList(MANY);

    const summaries = Array.from(document.querySelectorAll(".child-tickets-summary")).map((s) => s.textContent);
    expect(summaries).toEqual(["Blocked2", "In Progress5", "Backlog3", "Done10"]);
    expect(groupFor("Blocked")).toHaveAttribute("open");
    expect(groupFor("Done")).not.toHaveAttribute("open");
  });

  it("filters by title or id, keeps a match under its parent, and opens the groups it hit", () => {
    const parent = node(30, "done", [node(31, "backlog")]);
    parent.children[0].title = "Needle in a haystack";
    renderList([...MANY, parent]);

    fireEvent.change(screen.getByRole("searchbox", { name: "Filter child tickets by title or id" }), {
      target: { value: "needle" },
    });

    // Only the Done group holds a match (through the child), and it opens.
    const summaries = Array.from(document.querySelectorAll(".child-tickets-summary")).map((s) => s.textContent);
    expect(summaries).toEqual(["Done1"]);
    expect(groupFor("Done")).toHaveAttribute("open");
    expect(within(groupFor("Done")).getByText("Child 30")).toBeInTheDocument();
  });

  it("says what it searched for when nothing matches, and Escape or the button clears it", () => {
    renderList(MANY);
    const field = screen.getByRole("searchbox", { name: "Filter child tickets by title or id" });

    fireEvent.change(field, { target: { value: "zzz" } });
    expect(screen.getByText(/“zzz”/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear the filter" }));
    expect(field).toHaveValue("");

    fireEvent.change(field, { target: { value: "child 1" } });
    fireEvent.keyDown(field, { key: "Escape" });
    expect(field).toHaveValue("");
  });

  it("opens a child from its row", () => {
    const { onSelect } = renderList(MANY);
    fireEvent.click(screen.getByText("Child 3"));
    expect(onSelect).toHaveBeenCalledWith("c-3");
  });
});

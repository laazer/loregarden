import { fireEvent, render, screen } from "@testing-library/react";

import type { TicketTreeNode } from "../../api/client";
import { TicketTree } from "../TicketTree";

function makeNode(overrides: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id: "t1",
    external_id: "lg-one-1",
    title: "Make the console list scale",
    state: "blocked",
    priority: 1,
    work_item_type: "feature",
    workflow_stage_name: "Review",
    workflow_stage_status: "awaiting",
    workspace_slug: "loregarden",
    blocked_on: "person",
    child_count: 0,
    children: [],
    ...overrides,
  };
}

describe("TicketTree compact rows", () => {
  it("puts a ticket on one line and keeps its state readable without a mouse", () => {
    render(
      <TicketTree
        nodes={[makeNode()]}
        selectedId={null}
        expandedIds={new Set()}
        onSelect={() => {}}
        onToggle={() => {}}
        presentation="compact"
      />,
    );

    const row = screen.getByRole("treeitem");
    expect(row).toHaveClass("tree-row--compact");
    expect(row).toHaveTextContent("P1, Blocked · Needs you, Review awaiting");
    expect(row.getAttribute("title")).toContain("lg-one-1");
    expect(row.querySelector(".tree-card-meta")).toBeNull();
  });

  it("expands from a named chevron without selecting the row", () => {
    const onToggle = jest.fn();
    const onSelect = jest.fn();
    render(
      <TicketTree
        nodes={[makeNode({ children: [makeNode({ id: "t2", title: "Child" })], child_count: 1 })]}
        selectedId={null}
        expandedIds={new Set()}
        onSelect={onSelect}
        onToggle={onToggle}
        presentation="compact"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Expand Make the console list scale" }));
    expect(onToggle).toHaveBeenCalledWith("t1");
    expect(onSelect).not.toHaveBeenCalled();
  });
});

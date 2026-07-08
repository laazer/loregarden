import { render, screen } from "@testing-library/react";

import type { TicketTreeNode } from "../../api/client";
import { TicketTree } from "../TicketTree";

const noop = () => {};

function makeNode(overrides: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id: "t1",
    external_id: "LG-1",
    title: "Wire CLI agent runner for stage execution",
    state: "done",
    priority: 2,
    work_item_type: "task",
    workflow_stage_name: "Testing",
    workflow_stage_status: "blocked",
    workspace_slug: "loregarden",
    child_count: 0,
    children: [],
    ...overrides,
  };
}

describe("TicketTree layout structure", () => {
  it("renders title, meta, and workflow as direct grid children of the card row", () => {
    const { container } = render(
      <TicketTree
        nodes={[makeNode()]}
        selectedId={null}
        expandedIds={new Set()}
        onSelect={noop}
        onToggle={noop}
      />,
    );

    const row = container.querySelector(".tree-row");
    expect(row).not.toBeNull();

    const title = screen.getByText("Wire CLI agent runner for stage execution");
    expect(title).toHaveClass("tree-card-title");
    expect(title.parentElement).toBe(row);

    const meta = container.querySelector(".tree-card-meta");
    const workflow = container.querySelector(".tree-card-workflow");
    expect(meta?.parentElement).toBe(row);
    expect(workflow?.parentElement).toBe(row);
    expect(meta?.querySelector(".tree-card-meta-main")).not.toBeNull();
    expect(container.querySelector(".tree-card-body")).toBeNull();
    expect(container.querySelector(".tree-row-main")).toBeNull();
  });

  it("renders trail actions inside the meta row when present", () => {
    const { container } = render(
      <TicketTree
        nodes={[makeNode({ work_item_type: "feature", child_count: 2, children: [makeNode({ id: "c1" })] })]}
        selectedId={null}
        expandedIds={new Set()}
        onSelect={noop}
        onToggle={noop}
        onAddChild={noop}
      />,
    );

    const meta = container.querySelector(".tree-card-meta");
    expect(meta?.querySelector(".tree-row-trail")).not.toBeNull();
    expect(meta?.querySelector(".tree-card-meta-main")).not.toBeNull();
  });
});

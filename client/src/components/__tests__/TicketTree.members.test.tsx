import { fireEvent, render, screen, within } from "@testing-library/react";

import type { TicketTreeNode } from "../../api/client";
import { findTicketTreeNode } from "../../lib/parentTicketTree";
import { findAncestorIds, TicketTree } from "../TicketTree";

function node(id: string, overrides: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id,
    external_id: id,
    title: `Ticket ${id}`,
    state: "backlog",
    priority: 2,
    work_item_type: "feature",
    workflow_stage_name: "",
    workflow_stage_status: "pending",
    workspace_slug: "loregarden",
    child_count: 0,
    children: [],
    ...overrides,
  };
}

// An initiative tracking feature ft-1 as a member; ft-1 really lives under ms-1.
const capability = node("cap-1", { work_item_type: "capability" });
const feature = node("ft-1", { children: [capability], child_count: 1 });
const memberCopy = node("ft-1", { member_link: true, home_parent_external_id: "ms-1" });
const tree: TicketTreeNode[] = [
  node("init-1", { work_item_type: "initiative", workspace_slug: "", children: [memberCopy], child_count: 1 }),
  node("ms-1", { work_item_type: "milestone", children: [feature], child_count: 1 }),
];

describe("initiative members in the ticket tree", () => {
  it("resolves a ticket to its real row, not its member copy", () => {
    expect(findAncestorIds(tree, "ft-1")).toEqual(["ms-1"]);
    expect(findTicketTreeNode(tree, "ft-1")?.children).toEqual([capability]);
  });

  it("renders the member under the initiative, naming its real parent, with no add-child", () => {
    const onSelect = jest.fn();
    render(
      <div className="lg-primitive-ticket-list--v6">
        <TicketTree
          nodes={tree}
          selectedId={null}
          expandedIds={new Set(["init-1"])}
          onSelect={onSelect}
          onToggle={() => {}}
          onAddChild={() => {}}
          presentation="v6"
        />
      </div>,
    );

    const initiativeGroup = screen.getAllByRole("group")[0];
    const memberRow = within(initiativeGroup).getByRole("treeitem");
    expect(memberRow).toHaveClass("tree-row--member");
    expect(memberRow).toHaveTextContent("ms-1");
    expect(within(memberRow).queryByRole("button", { name: /add/i })).toBeNull();

    fireEvent.click(memberRow);
    expect(onSelect).toHaveBeenCalledWith("ft-1");
  });
});

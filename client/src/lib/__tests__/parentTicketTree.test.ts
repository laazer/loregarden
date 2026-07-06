import type { TicketTreeNode } from "../../api/client";
import { filterTreeEligibleParents, findTicketTreeNode, parentTypesForChild } from "../parentTicketTree";

const tree: TicketTreeNode[] = [
  {
    id: "ms-1",
    external_id: "01-milestone",
    title: "Bootstrap",
    state: "in_progress",
    priority: 2,
    work_item_type: "milestone",
    workflow_stage_name: "Planning",
    workflow_stage_status: "pending",
    child_count: 1,
    children: [
      {
        id: "feat-1",
        external_id: "02-feature",
        title: "Backend",
        state: "backlog",
        priority: 2,
        work_item_type: "feature",
        workflow_stage_name: "Planning",
        workflow_stage_status: "pending",
        child_count: 1,
        children: [
          {
            id: "cap-1",
            external_id: "03-capability",
            title: "API core",
            state: "backlog",
            priority: 2,
            work_item_type: "capability",
            workflow_stage_name: "Planning",
            workflow_stage_status: "pending",
            child_count: 0,
            children: [],
          },
        ],
      },
    ],
  },
];

describe("parentTicketTree", () => {
  it("finds nodes by id", () => {
    expect(findTicketTreeNode(tree, "cap-1")?.title).toBe("API core");
    expect(findTicketTreeNode(tree, "missing")).toBeNull();
  });

  it("filters eligible parents for tasks", () => {
    const allowed = parentTypesForChild("task");
    const filtered = filterTreeEligibleParents(tree, allowed);
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe("ms-1");
    expect(filtered[0].children[0].id).toBe("feat-1");
    expect(filtered[0].children[0].children[0].id).toBe("cap-1");
  });

  it("filters eligible parents for features", () => {
    const filtered = filterTreeEligibleParents(tree, parentTypesForChild("feature"));
    expect(filtered).toHaveLength(1);
    expect(filtered[0].children).toHaveLength(0);
  });
});

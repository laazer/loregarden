import type { TicketTreeNode } from "../../api/types";
import { hasParts, partStanding, partsProgress, partsTag } from "../ticketParts";
import { flattenWorkItems } from "../workItemsTable";

function node(id: string, state: TicketTreeNode["state"], extra: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id,
    external_id: id,
    title: id,
    state,
    priority: 1,
    work_item_type: "task",
    workflow_stage_name: "",
    workflow_stage_status: "pending",
    child_count: 0,
    children: [],
    ...extra,
  };
}

describe("ticket parts", () => {
  it("treats a split ticket or a bug with sub-items as having parts, and containers as not", () => {
    expect(hasParts({ work_item_type: "feature", was_split: true })).toBe(true);
    expect(hasParts({ work_item_type: "bug" })).toBe(true);
    expect(hasParts({ work_item_type: "milestone" })).toBe(false);
    expect(hasParts({ work_item_type: "feature" })).toBe(false);
  });

  it("reads a part's standing from its state", () => {
    expect(partStanding(node("a", "done"))).toBe("done");
    expect(partStanding(node("a", "wont_do"))).toBe("done");
    expect(partStanding(node("a", "blocked"))).toBe("blocked");
    expect(partStanding(node("a", "in_progress"))).toBe("running");
    expect(partStanding(node("a", "backlog"))).toBe("waiting");
  });

  it("opens the running part first, else the first part not done", () => {
    const running = partsProgress([node("a", "done"), node("b", "backlog"), node("c", "in_progress")]);
    expect(running).toMatchObject({ total: 3, done: 1, running: true });
    expect(running.current?.id).toBe("c");

    const idle = partsProgress([node("a", "done"), node("b", "backlog")]);
    expect(idle.running).toBe(false);
    expect(idle.current?.id).toBe("b");

    expect(partsProgress([node("a", "done")]).current).toBeNull();
  });

  it("tags progress, and says done once every part is", () => {
    expect(partsTag([node("a", "done"), node("b", "backlog")])).toBe("2 parts · 1 done");
    expect(partsTag([node("a", "done"), node("b", "wont_do")])).toBe("2 parts · done");
  });

  it("labels each Work items row that is one part of a split ticket", () => {
    const tree = [
      node("parent", "in_progress", {
        work_item_type: "feature",
        was_split: true,
        children: [node("p1", "done"), node("p2", "backlog")],
      }),
      node("milestone", "in_progress", { work_item_type: "milestone", children: [node("f1", "backlog")] }),
    ];

    const labels = Object.fromEntries(flattenWorkItems(tree).map((row) => [row.node.id, row.partLabel]));

    expect(labels).toEqual({ parent: undefined, p1: "part 1/2", p2: "part 2/2", milestone: undefined, f1: undefined });
  });
});

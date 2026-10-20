import type { TicketTreeNode } from "../../api/types";
import {
  flattenWorkItems,
  inView,
  matchesSearch,
  needsMeReason,
  parseWorkItemsView,
  sortWorkItems,
  viewCounts,
  workspaceOptions,
} from "../workItemsTable";

function node(id: string, overrides: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id,
    external_id: `lg-${id}`,
    title: `Ticket ${id}`,
    state: "backlog",
    priority: 2,
    work_item_type: "feature",
    workspace_slug: "loregarden",
    workflow_stage_name: "",
    workflow_stage_status: "pending",
    child_count: 0,
    children: [],
    ...overrides,
  };
}

describe("flattenWorkItems", () => {
  it("lists every level once, each with the parent it sits under", () => {
    const leaf = node("c");
    const mid = node("b", { children: [leaf], child_count: 1 });
    const root = node("a", { children: [mid], child_count: 1 });

    const rows = flattenWorkItems([root]);

    expect(rows.map((r) => [r.node.id, r.parent?.id ?? null])).toEqual([
      ["a", null],
      ["b", "a"],
      ["c", "b"],
    ]);
  });

  it("skips an initiative's member copy so the ticket appears once, under its real parent", () => {
    const real = node("m");
    const copy: TicketTreeNode = { ...real, member_link: true };
    const initiative = node("i", { work_item_type: "initiative", children: [copy] });
    const milestone = node("ms", { children: [real] });

    const rows = flattenWorkItems([initiative, milestone]);

    expect(rows.filter((r) => r.node.id === "m").map((r) => r.parent?.id)).toEqual(["ms"]);
  });
});

describe("needsMeReason", () => {
  it("flags a stage waiting on an approval or answer", () => {
    const n = node("a", { workflow_stage_name: "Review", workflow_stage_status: "awaiting", state: "in_progress" });
    expect(needsMeReason(n)).toBe("Your approval or answer");
  });

  it("flags a block only a person can clear", () => {
    expect(needsMeReason(node("a", { state: "blocked", blocked_on: "person" }))).not.toBeNull();
  });

  it("leaves out blocks an agent or another ticket clears, and closed tickets", () => {
    expect(needsMeReason(node("a", { state: "blocked", blocked_on: "agent" }))).toBeNull();
    expect(needsMeReason(node("b", { blocked_on: "tickets" }))).toBeNull();
    expect(needsMeReason(node("c", { state: "done", workflow_stage_status: "awaiting" }))).toBeNull();
  });
});

describe("views", () => {
  const rows = flattenWorkItems([
    node("await", { state: "in_progress", workflow_stage_status: "awaiting" }),
    node("run", { state: "in_progress" }),
    node("stuck", { state: "blocked", blocked_on: "agent" }),
    node("todo"),
    node("shipped", { state: "done" }),
  ]);

  it("counts each view over the same rows", () => {
    expect(viewCounts(rows)).toEqual({ needs_me: 1, in_flight: 2, blocked: 1, open: 4, all: 5 });
  });

  it("puts a ticket in every view whose filter it meets", () => {
    const awaiting = rows[0].node;
    expect(inView("needs_me", awaiting) && inView("in_flight", awaiting) && inView("open", awaiting)).toBe(true);
    expect(inView("blocked", awaiting)).toBe(false);
  });

  it("defaults an unknown or missing view to needs_me", () => {
    expect(parseWorkItemsView(null)).toBe("needs_me");
    expect(parseWorkItemsView("bogus")).toBe("needs_me");
    expect(parseWorkItemsView("open")).toBe("open");
  });
});

describe("matchesSearch", () => {
  it("matches the title, the id, or the parent's title", () => {
    const [, child] = flattenWorkItems([node("p", { title: "Memory layer", children: [node("k", { title: "Index" })] })]);
    expect(matchesSearch(child, "memory")).toBe(true);
    expect(matchesSearch(child, "LG-K")).toBe(true);
    expect(matchesSearch(child, "unrelated")).toBe(false);
    expect(matchesSearch(child, "  ")).toBe(true);
  });
});

describe("sortWorkItems", () => {
  const rows = flattenWorkItems([
    node("b", { priority: 2, workspace_slug: "blobert" }),
    node("a", { priority: 1, workspace_slug: "loregarden" }),
    node("c", { priority: 2, workspace_slug: "blobert" }),
  ]);

  it("sorts by the column, then priority and id for ties", () => {
    const sorted = sortWorkItems(rows, { key: "workspace", dir: "ascending" });
    expect(sorted.map((r) => r.node.id)).toEqual(["b", "c", "a"]);
  });

  it("reverses only the chosen column, keeping ties in id order", () => {
    const sorted = sortWorkItems(rows, { key: "priority", dir: "descending" });
    expect(sorted.map((r) => r.node.id)).toEqual(["b", "c", "a"]);
  });

  it("does not reorder the rows it was given", () => {
    sortWorkItems(rows, { key: "priority", dir: "ascending" });
    expect(rows.map((r) => r.node.id)).toEqual(["b", "a", "c"]);
  });
});

describe("workspaceOptions", () => {
  it("lists workspaces with their counts, largest first", () => {
    const rows = flattenWorkItems([
      node("a", { workspace_slug: "blobert" }),
      node("b"),
      node("c"),
    ]);
    expect(workspaceOptions(rows)).toEqual([
      ["loregarden", 2],
      ["blobert", 1],
    ]);
  });
});

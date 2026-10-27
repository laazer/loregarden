import type { TicketTreeNode } from "../../api/types";
import {
  NO_WORK_ITEMS_FILTERS,
  activeFilterCount,
  flattenWorkItems,
  inView,
  matchesFilters,
  matchesSearch,
  parseWorkItemsFilters,
  needsMeReason,
  parseWorkItemsView,
  sortWorkItems,
  viewCounts,
  workspaceOptions,
  writeWorkItemsFilters,
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

  const none = new Set<string>();

  it("counts each view over the same rows", () => {
    expect(viewCounts(rows, new Set(["run"]))).toEqual({
      needs_me: 1,
      running: 1,
      in_flight: 2,
      blocked: 1,
      open: 4,
      all: 5,
    });
  });

  it("puts a ticket in every view whose filter it meets", () => {
    const awaiting = rows[0].node;
    expect(inView("needs_me", awaiting, none) && inView("in_flight", awaiting, none) && inView("open", awaiting, none)).toBe(
      true,
    );
    expect(inView("blocked", awaiting, none)).toBe(false);
  });

  it("calls a ticket running only while the queue holds a slot for it, whatever its stage says", () => {
    const idle = rows[1].node;
    expect(inView("running", idle, none)).toBe(false);
    expect(inView("running", idle, new Set(["run"]))).toBe(true);
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

describe("filters", () => {
  const rows = flattenWorkItems([
    node("ms", {
      work_item_type: "milestone",
      priority: 1,
      children: [
        node("bug", { work_item_type: "bug", state: "blocked", blocked_on: "person", priority: 1 }),
        node("impl", { state: "in_progress", workflow_stage_name: "Implement", workflow_stage_status: "running", priority: 3 }),
      ],
    }),
  ]);
  const ids = (f: typeof NO_WORK_ITEMS_FILTERS) => rows.filter((r) => matchesFilters(r, f)).map((r) => r.node.id);

  it("keeps everything with no filter set", () => {
    expect(ids(NO_WORK_ITEMS_FILTERS)).toEqual(["ms", "bug", "impl"]);
    expect(activeFilterCount(NO_WORK_ITEMS_FILTERS)).toBe(0);
  });

  it("narrows by each field, any-of within one and all-of across them", () => {
    expect(ids({ ...NO_WORK_ITEMS_FILTERS, types: ["bug", "milestone"] })).toEqual(["ms", "bug"]);
    expect(ids({ ...NO_WORK_ITEMS_FILTERS, types: ["bug", "milestone"], priorities: [1], states: ["blocked"] })).toEqual([
      "bug",
    ]);
    expect(ids({ ...NO_WORK_ITEMS_FILTERS, blockedOn: ["person"] })).toEqual(["bug"]);
    expect(ids({ ...NO_WORK_ITEMS_FILTERS, topLevelOnly: true })).toEqual(["ms"]);
  });

  it("matches a stage status only on a ticket that has a stage", () => {
    expect(ids({ ...NO_WORK_ITEMS_FILTERS, stageStatuses: ["pending"] })).toEqual([]);
    expect(ids({ ...NO_WORK_ITEMS_FILTERS, stageStatuses: ["running"] })).toEqual(["impl"]);
  });

  it("round-trips through the URL, leaving defaults and other params alone", () => {
    const filters = { ...NO_WORK_ITEMS_FILTERS, workspace: "blobert", states: ["parked" as const], priorities: [1, 2], topLevelOnly: true };
    const params = writeWorkItemsFilters(new URLSearchParams("view=all"), filters);
    expect(params.toString()).toBe("view=all&ws=blobert&state=parked&prio=1%2C2&top=1");
    expect(parseWorkItemsFilters(params)).toEqual(filters);
    expect(activeFilterCount(filters)).toBe(4);
    expect(writeWorkItemsFilters(params, NO_WORK_ITEMS_FILTERS).toString()).toBe("view=all");
  });

  it("drops values a stale link names that no longer exist", () => {
    expect(parseWorkItemsFilters(new URLSearchParams("state=parked,bogus&prio=9")).states).toEqual(["parked"]);
    expect(parseWorkItemsFilters(new URLSearchParams("prio=9")).priorities).toEqual([]);
  });
});

describe("needsMeReason with the ticket's approval", () => {
  const awaiting = {
    id: "t1",
    external_id: "lg-1",
    title: "T",
    state: "in_progress",
    priority: 2,
    work_item_type: "bug",
    workspace_slug: "loregarden",
    workflow_stage_name: "Implement",
    workflow_stage_status: "awaiting",
    child_count: 0,
    children: [],
  } as unknown as Parameters<typeof needsMeReason>[0];

  it("names what the approval asks, falling back to its kind, then to the generic reason", () => {
    expect(needsMeReason(awaiting, { title: "Resolve Playtest exit actions", kind: "workflow_gate" } as never)).toBe(
      "Resolve Playtest exit actions",
    );
    expect(needsMeReason(awaiting, { title: "", kind: "cli_question" } as never)).toBe("Question");
    expect(needsMeReason(awaiting)).toBe("Your approval or answer");
  });
});

import type { TicketTreeNode, WorkspaceSummary } from "../../api/types";
import { archivedWorkspaceSlugs, withoutArchivedWorkspaces } from "../archivedWorkspaces";

const node = (id: string, workspace_slug: string | undefined, children: TicketTreeNode[] = []): TicketTreeNode => ({
  id,
  external_id: id,
  title: id,
  state: "backlog",
  priority: 3,
  work_item_type: "task",
  workspace_slug,
  workflow_stage_name: "",
  workflow_stage_status: "pending",
  child_count: children.length,
  children,
});

const ids = (nodes: TicketTreeNode[]): string[] => nodes.flatMap((n) => [n.id, ...ids(n.children)]);

it("collects only archived slugs", () => {
  const rows = [
    { slug: "shop", archived_at: null },
    { slug: "old", archived_at: "2026-09-30T00:00:00Z" },
  ] as WorkspaceSummary[];
  expect([...archivedWorkspaceSlugs(rows)]).toEqual(["old"]);
});

it("drops an archived workspace's items at any depth, and keeps workspaceless ones", () => {
  const tree = [
    node("shop-1", "shop", [node("old-child", "old")]),
    node("old-1", "old", [node("old-2", "old")]),
    node("initiative", undefined, [node("shop-2", "shop"), node("old-3", "old")]),
  ];
  expect(ids(withoutArchivedWorkspaces(tree, new Set(["old"])))).toEqual(["shop-1", "initiative", "shop-2"]);
});

it("returns the same tree when nothing is archived, so memos keyed on it hold", () => {
  const tree = [node("shop-1", "shop")];
  expect(withoutArchivedWorkspaces(tree, new Set())).toBe(tree);
});

/**
 * The Work items page's rows: the ticket tree flattened, split into views, and
 * sorted. Pure, so the page renders what these functions return and the tests
 * check them without a DOM.
 *
 * Built from `/api/tickets/tree` rather than the flat `/api/tickets`: on the
 * live database (1,665 tickets) the tree is about 0.7 MB and 3 s, the flat list
 * 5.7 MB and 14 s, because every flat row carries its full stage list.
 */

import type { TicketState, TicketTreeNode } from "../api/types";
import { blockedOnStateLabel } from "../utils/blockKinds";

export interface WorkItemParent {
  id: string;
  external_id: string;
  title: string;
}

export interface WorkItemRow {
  node: TicketTreeNode;
  parent: WorkItemParent | null;
}

export type WorkItemsView = "needs_me" | "in_flight" | "blocked" | "open" | "all";

export const WORK_ITEMS_VIEWS: { key: WorkItemsView; label: string }[] = [
  { key: "needs_me", label: "Needs me" },
  { key: "in_flight", label: "In progress" },
  { key: "blocked", label: "Blocked" },
  { key: "open", label: "Open" },
  { key: "all", label: "All" },
];

export function parseWorkItemsView(value: string | null): WorkItemsView {
  return WORK_ITEMS_VIEWS.find((view) => view.key === value)?.key ?? "needs_me";
}

const CLOSED_STATES: ReadonlySet<TicketState> = new Set<TicketState>(["done", "wont_do"]);

export function isOpen(node: TicketTreeNode): boolean {
  return !CLOSED_STATES.has(node.state);
}

/**
 * Why an open ticket is the operator's to act on, or null when it is not.
 *
 * Two signals the server already derives: a stage paused for an approval or an
 * answer (`awaiting`, which is what fills the inbox), and a block only a person
 * can clear (`blocked_on === "person"`, the board's "Needs you" column).
 */
export function needsMeReason(node: TicketTreeNode): string | null {
  if (!isOpen(node)) return null;
  if (node.workflow_stage_status === "awaiting") {
    return "Your approval or answer";
  }
  if (node.blocked_on === "person") return "Blocked until you act";
  return null;
}

const VIEW_FILTERS: Record<WorkItemsView, (node: TicketTreeNode) => boolean> = {
  needs_me: (node) => needsMeReason(node) !== null,
  in_flight: (node) => node.state === "in_progress",
  blocked: (node) => node.state === "blocked",
  open: isOpen,
  all: () => true,
};

export function inView(view: WorkItemsView, node: TicketTreeNode): boolean {
  return VIEW_FILTERS[view](node);
}

/** Every ticket in the tree once, with the parent it sits under. */
export function flattenWorkItems(nodes: TicketTreeNode[]): WorkItemRow[] {
  const rows: WorkItemRow[] = [];
  const seen = new Set<string>();
  const walk = (items: TicketTreeNode[], parent: WorkItemParent | null) => {
    for (const node of items) {
      // An initiative's member copy points at a ticket that also appears
      // under its real parent; list it there, once.
      if (node.member_link || seen.has(node.id)) continue;
      seen.add(node.id);
      rows.push({ node, parent });
      walk(node.children, { id: node.id, external_id: node.external_id, title: node.title });
    }
  };
  walk(nodes, null);
  return rows;
}

export function viewCounts(rows: WorkItemRow[]): Record<WorkItemsView, number> {
  const counts: Record<WorkItemsView, number> = { needs_me: 0, in_flight: 0, blocked: 0, open: 0, all: 0 };
  for (const { node } of rows) {
    for (const { key } of WORK_ITEMS_VIEWS) {
      if (VIEW_FILTERS[key](node)) counts[key] += 1;
    }
  }
  return counts;
}

export function matchesSearch(row: WorkItemRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [row.node.title, row.node.external_id, row.parent?.title ?? "", row.parent?.external_id ?? ""].some(
    (text) => text.toLowerCase().includes(needle),
  );
}

export type WorkItemsSortKey = "priority" | "id" | "title" | "state" | "stage" | "workspace" | "parent";
export type SortDir = "ascending" | "descending";
export interface WorkItemsSort {
  key: WorkItemsSortKey;
  dir: SortDir;
}

export const DEFAULT_WORK_ITEMS_SORT: WorkItemsSort = { key: "priority", dir: "ascending" };

export function stateText(node: TicketTreeNode): string {
  return blockedOnStateLabel(node.state, node.blocked_on);
}

export function stageText(node: TicketTreeNode): string {
  return node.workflow_stage_name
    ? `${node.workflow_stage_name} · ${node.workflow_stage_status.replace("_", " ")}`
    : "";
}

const SORT_VALUE: Record<WorkItemsSortKey, (row: WorkItemRow) => string | number> = {
  priority: (row) => row.node.priority,
  id: (row) => row.node.external_id,
  title: (row) => row.node.title.toLowerCase(),
  state: (row) => stateText(row.node),
  stage: (row) => stageText(row.node),
  workspace: (row) => row.node.workspace_slug ?? "",
  parent: (row) => row.parent?.title.toLowerCase() ?? "",
};

/**
 * Sort by the chosen column, then priority, then id, so equal values keep a
 * stable, meaningful order instead of the tree's.
 */
export function sortWorkItems(rows: WorkItemRow[], sort: WorkItemsSort): WorkItemRow[] {
  const sign = sort.dir === "ascending" ? 1 : -1;
  const primary = SORT_VALUE[sort.key];
  const compare = (a: string | number, b: string | number) =>
    typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
  return [...rows].sort(
    (a, b) =>
      sign * compare(primary(a), primary(b)) ||
      a.node.priority - b.node.priority ||
      a.node.external_id.localeCompare(b.node.external_id),
  );
}

/**
 * A ticket's id, state, stage and workspace in one tooltip, for a row too
 * narrow to show them (the Console's one-line rows).
 */
export function ticketTooltip(node: TicketTreeNode): string {
  return [
    `${node.external_id} · ${node.title}`,
    `P${node.priority} · ${stateText(node)}`,
    stageText(node),
    node.workspace_slug ?? "",
  ]
    .filter(Boolean)
    .join("\n");
}

/** Workspaces present in the rows, most tickets first, for the filter. */
export function workspaceOptions(rows: WorkItemRow[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const { node } of rows) {
    if (node.workspace_slug) counts.set(node.workspace_slug, (counts.get(node.workspace_slug) ?? 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

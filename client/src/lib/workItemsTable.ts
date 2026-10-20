/**
 * The Work items page's rows: the ticket tree flattened, split into views, and
 * sorted. Pure, so the page renders what these functions return and the tests
 * check them without a DOM.
 *
 * Built from `/api/tickets/tree` rather than the flat `/api/tickets`: on the
 * live database (1,665 tickets) the tree is about 0.7 MB and 3 s, the flat list
 * 5.7 MB and 14 s, because every flat row carries its full stage list.
 */

import type { BlockedOn, StageStatus, TicketState, TicketTreeNode, WorkItemType } from "../api/types";
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

export type WorkItemsView = "needs_me" | "running" | "in_flight" | "blocked" | "open" | "all";

export const WORK_ITEMS_VIEWS: { key: WorkItemsView; label: string }[] = [
  { key: "needs_me", label: "Needs me" },
  { key: "running", label: "Running" },
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

/**
 * Ticket ids holding an execution slot now, from the queue's `activeRuns`. The
 * tree's `workflow_stage_status` cannot say this: a lane holds its slot between
 * stages, when no stage reads "running".
 */
export type RunningIds = ReadonlySet<string>;

const VIEW_FILTERS: Record<WorkItemsView, (node: TicketTreeNode, running: RunningIds) => boolean> = {
  needs_me: (node) => needsMeReason(node) !== null,
  running: (node, running) => running.has(node.id),
  in_flight: (node) => node.state === "in_progress",
  blocked: (node) => node.state === "blocked",
  open: isOpen,
  all: () => true,
};

export function inView(view: WorkItemsView, node: TicketTreeNode, running: RunningIds): boolean {
  return VIEW_FILTERS[view](node, running);
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

export function viewCounts(rows: WorkItemRow[], running: RunningIds): Record<WorkItemsView, number> {
  const counts: Record<WorkItemsView, number> = { needs_me: 0, running: 0, in_flight: 0, blocked: 0, open: 0, all: 0 };
  for (const { node } of rows) {
    for (const { key } of WORK_ITEMS_VIEWS) {
      if (VIEW_FILTERS[key](node, running)) counts[key] += 1;
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

/**
 * The filters beside the views, kept in the URL so a filtered list can be
 * linked and survives a reload. An empty list means "any".
 */
export interface WorkItemsFilters {
  workspace: string;
  states: TicketState[];
  types: WorkItemType[];
  priorities: number[];
  stageStatuses: StageStatus[];
  blockedOn: BlockedOn[];
  topLevelOnly: boolean;
}

export const TICKET_STATE_OPTIONS: TicketState[] = ["backlog", "in_progress", "blocked", "parked", "done", "wont_do"];
export const WORK_ITEM_TYPE_OPTIONS: WorkItemType[] = ["initiative", "milestone", "feature", "capability", "task", "bug"];
export const PRIORITY_OPTIONS = [1, 2, 3] as const;
export const STAGE_STATUS_OPTIONS: StageStatus[] = ["pending", "running", "awaiting", "blocked", "done", "wont_do"];
export const BLOCKED_ON_OPTIONS: BlockedOn[] = ["person", "tickets", "agent"];

type ListFilterKey = "states" | "types" | "priorities" | "stageStatuses" | "blockedOn";

const LIST_PARAMS: Record<ListFilterKey, string> = {
  states: "state",
  types: "type",
  priorities: "prio",
  stageStatuses: "stage",
  blockedOn: "blocked",
};

/** Values outside the known set are dropped, so a stale link narrows to what still exists. */
function parseList<T extends string | number>(raw: string | null, allowed: readonly T[]): T[] {
  if (!raw) return [];
  const parts = raw.split(",");
  return allowed.filter((value) => parts.includes(String(value)));
}

export function parseWorkItemsFilters(params: URLSearchParams): WorkItemsFilters {
  return {
    workspace: params.get("ws") || "all",
    states: parseList(params.get(LIST_PARAMS.states), TICKET_STATE_OPTIONS),
    types: parseList(params.get(LIST_PARAMS.types), WORK_ITEM_TYPE_OPTIONS),
    priorities: parseList(params.get(LIST_PARAMS.priorities), PRIORITY_OPTIONS),
    stageStatuses: parseList(params.get(LIST_PARAMS.stageStatuses), STAGE_STATUS_OPTIONS),
    blockedOn: parseList(params.get(LIST_PARAMS.blockedOn), BLOCKED_ON_OPTIONS),
    topLevelOnly: params.get("top") === "1",
  };
}

/** The params with `filters` written over them; defaults are left out of the URL. */
export function writeWorkItemsFilters(params: URLSearchParams, filters: WorkItemsFilters): URLSearchParams {
  const out = new URLSearchParams(params);
  if (filters.workspace === "all") out.delete("ws");
  else out.set("ws", filters.workspace);
  for (const [key, param] of Object.entries(LIST_PARAMS) as [ListFilterKey, string][]) {
    const values = filters[key];
    if (values.length === 0) out.delete(param);
    else out.set(param, values.join(","));
  }
  if (filters.topLevelOnly) out.set("top", "1");
  else out.delete("top");
  return out;
}

export function activeFilterCount(filters: WorkItemsFilters): number {
  return (
    (filters.workspace === "all" ? 0 : 1) +
    (Object.keys(LIST_PARAMS) as ListFilterKey[]).filter((key) => filters[key].length > 0).length +
    (filters.topLevelOnly ? 1 : 0)
  );
}

export const NO_WORK_ITEMS_FILTERS: WorkItemsFilters = {
  workspace: "all",
  states: [],
  types: [],
  priorities: [],
  stageStatuses: [],
  blockedOn: [],
  topLevelOnly: false,
};

/** Every filter except the workspace, which the view counts are already scoped by. */
export function matchesFilters(row: WorkItemRow, filters: WorkItemsFilters): boolean {
  const { node } = row;
  return (
    (filters.states.length === 0 || filters.states.includes(node.state)) &&
    (filters.types.length === 0 || filters.types.includes(node.work_item_type)) &&
    (filters.priorities.length === 0 || filters.priorities.includes(node.priority)) &&
    (filters.stageStatuses.length === 0 ||
      (node.workflow_stage_name !== "" && filters.stageStatuses.includes(node.workflow_stage_status))) &&
    (filters.blockedOn.length === 0 || (node.blocked_on != null && filters.blockedOn.includes(node.blocked_on))) &&
    (!filters.topLevelOnly || row.parent === null)
  );
}

/**
 * The rows every filter but one keeps, so a menu's counts say what picking a
 * value would leave given the other filters already set.
 */
export function rowsForFacet(rows: WorkItemRow[], filters: WorkItemsFilters, facet: keyof WorkItemsFilters): WorkItemRow[] {
  const others = { ...filters, [facet]: NO_WORK_ITEMS_FILTERS[facet] };
  return rows.filter((row) => matchesFilters(row, others));
}

/** How many rows carry each value of one field, for the counts in a filter menu. */
export function countBy<T extends string | number>(
  rows: WorkItemRow[],
  value: (node: TicketTreeNode) => T | null | undefined,
): Map<T, number> {
  const counts = new Map<T, number>();
  for (const { node } of rows) {
    const v = value(node);
    if (v != null) counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  return counts;
}

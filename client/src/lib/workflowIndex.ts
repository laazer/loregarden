/**
 * Workflow Studio's index: every workflow with how many tickets run on it,
 * most used first.
 *
 * A Studio workflow's tickets run on the template it publishes to, so usage is
 * looked up by `published_template_slug` when there is one, and by its own slug
 * for a template listed directly. A Studio workflow never published has no
 * tickets by construction, and is labelled a draft rather than shown as unused.
 */

import type { StudioWorkflow, WorkflowUsageStats } from "../api/types";
import type { SortDirection } from "./indexSort";

export type WorkflowKind = "built-in" | "custom" | "draft";

export interface WorkflowIndexRow {
  workflow: StudioWorkflow;
  kind: WorkflowKind;
  stages: number;
  tickets: number;
  open: number;
  lastUsedAt: string | null;
}

export type WorkflowSortKey = "name" | "stages" | "tickets" | "open" | "lastUsed";

function kindOf(workflow: StudioWorkflow): WorkflowKind {
  if (workflow.built_in) return "built-in";
  // A template listed directly carries its own id here, and a Studio workflow
  // carries its template's once published; only an unpublished draft has none.
  return workflow.published_template_id ? "custom" : "draft";
}

export function workflowIndexRows(
  workflows: readonly StudioWorkflow[],
  usage: readonly WorkflowUsageStats[],
): WorkflowIndexRow[] {
  const bySlug = new Map(usage.map((row) => [row.template_slug, row]));
  return workflows.map((workflow) => {
    const row = bySlug.get(workflow.published_template_slug || workflow.slug);
    return {
      workflow,
      kind: kindOf(workflow),
      stages: workflow.stages.length,
      tickets: row?.tickets ?? 0,
      open: row?.open ?? 0,
      lastUsedAt: row?.last_used_at ?? null,
    };
  });
}

/** Folded under one row by default: no ticket has ever run on it. */
export function isUnused(row: WorkflowIndexRow): boolean {
  return row.tickets === 0;
}

const COMPARE: Record<WorkflowSortKey, (a: WorkflowIndexRow, b: WorkflowIndexRow) => number> = {
  name: (a, b) => a.workflow.name.localeCompare(b.workflow.name),
  stages: (a, b) => a.stages - b.stages,
  tickets: (a, b) => a.tickets - b.tickets,
  open: (a, b) => a.open - b.open,
  lastUsed: (a, b) => (a.lastUsedAt ?? "").localeCompare(b.lastUsedAt ?? ""),
};

/** Sorted by `key`; ties go to the workflow with more tickets, then by name. */
export function sortWorkflowRows(
  rows: readonly WorkflowIndexRow[],
  key: WorkflowSortKey,
  direction: SortDirection,
): WorkflowIndexRow[] {
  const sign = direction === "ascending" ? 1 : -1;
  return [...rows].sort(
    (a, b) =>
      sign * COMPARE[key](a, b) ||
      b.tickets - a.tickets ||
      a.workflow.name.localeCompare(b.workflow.name),
  );
}

export function matchesWorkflowFilter(row: WorkflowIndexRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [row.workflow.name, row.workflow.slug, row.workflow.description].some((text) =>
    text.toLowerCase().includes(needle),
  );
}

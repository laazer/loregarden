/**
 * Workflow Studio's first screen: every workflow, most tickets first, each a
 * link to its editor. It replaces a blank "New workflow" form as the landing
 * page; New workflow… now opens a dialog from here.
 *
 * Workflows no ticket has used fold under one row, so the few carrying the
 * work are what the page leads with. Nothing folds while filtering, because a
 * search for one by name must find it.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import type { StudioWorkflow, WorkflowUsageStats } from "../../api/types";
import { studioWorkflowPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import {
  isUnused,
  matchesWorkflowFilter,
  sortWorkflowRows,
  workflowIndexRows,
  type WorkflowIndexRow,
  type WorkflowKind,
  type WorkflowSortKey,
} from "../../lib/workflowIndex";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import {
  IndexLoadError,
  IndexStatsWarning,
  NoMatch,
  SortableHead,
  StudioIndexFrame,
  type IndexQuery,
} from "./StudioIndex";
import { useIndexSort, type IndexColumn } from "../../lib/indexSort";

const COLUMNS: IndexColumn<WorkflowSortKey>[] = [
  { key: "name", label: "Workflow", numeric: false, first: "ascending" },
  { key: "stages", label: "Stages", numeric: true, first: "descending" },
  { key: "tickets", label: "Tickets", numeric: true, first: "descending" },
  { key: "open", label: "Open", numeric: true, first: "descending" },
  { key: "lastUsed", label: "Last used", numeric: true, first: "descending" },
];

const KIND_LABEL: Record<WorkflowKind, string> = {
  "built-in": "built-in",
  custom: "custom",
  draft: "draft, not published",
};

interface Props {
  workflows: IndexQuery<StudioWorkflow[]>;
  usage: IndexQuery<WorkflowUsageStats[]>;
  onNewWorkflow: () => void;
  newWorkflowOpen: boolean;
}

function WorkflowRow({ row, usageReady }: { row: WorkflowIndexRow; usageReady: boolean }) {
  const count = (value: number) => (usageReady ? value.toLocaleString() : "—");
  return (
    <tr>
      <th scope="row">
        <Link className="studio-index-name" to={studioWorkflowPath(row.workflow.slug)}>
          {row.workflow.name}
        </Link>
        <span className="studio-index-slug">
          {row.workflow.slug} · {KIND_LABEL[row.kind]}
        </span>
      </th>
      <td className="num" data-label="Stages">
        {row.stages}
      </td>
      <td className="num" data-label="Tickets">
        {count(row.tickets)}
      </td>
      <td className="num" data-label="Open">
        {count(row.open)}
      </td>
      <td className="num" data-label="Last used">
        {row.lastUsedAt ? (
          formatRelativeAge(row.lastUsedAt)
        ) : (
          <span className="studio-index-muted">{usageReady ? "never" : "—"}</span>
        )}
      </td>
    </tr>
  );
}

export function WorkflowIndex({ workflows, usage, onNewWorkflow, newWorkflowOpen }: Props) {
  const [query, setQuery] = useState("");
  const [unusedOpen, setUnusedOpen] = useState(false);
  const { sort, onSort } = useIndexSort<WorkflowSortKey>({ key: "tickets", dir: "descending" });

  if (workflows.isPending) return <PaneSkeleton variant="list" label="Loading workflows…" />;
  if (workflows.isError || !workflows.data) return <IndexLoadError what="workflows" query={workflows} />;

  const usageReady = Boolean(usage.data);
  const allRows = workflowIndexRows(workflows.data, usage.data ?? []);
  const rows = sortWorkflowRows(
    allRows.filter((row) => matchesWorkflowFilter(row, query)),
    sort.key,
    sort.dir,
  );
  // Fold only with counts in hand (without them every row reads as unused) and
  // never while a filter is narrowing the list.
  const fold = usageReady && !query.trim();
  const shown = fold ? rows.filter((row) => !isUnused(row)) : rows;
  const unused = fold ? rows.filter(isUnused) : [];
  const totalTickets = allRows.reduce((sum, row) => sum + row.tickets, 0);

  return (
    <StudioIndexFrame
      id="workflow-index-title"
      title="Workflows"
      summary={`${allRows.length} workflows${
        usageReady ? ` · ${totalTickets.toLocaleString()} tickets have run on them` : ""
      }`}
      noun="workflow"
      query={query}
      onQuery={setQuery}
      newLabel="New workflow…"
      onNew={onNewWorkflow}
      newOpen={newWorkflowOpen}
    >
      <IndexStatsWarning query={usage}>Ticket counts did not load</IndexStatsWarning>

      {allRows.length === 0 ? (
        <p className="studio-index-empty">
          No workflows yet. New workflow… creates one; built-in templates appear here once the
          server has seeded them.
        </p>
      ) : rows.length === 0 ? (
        <NoMatch noun="workflow" query={query} onClear={() => setQuery("")} />
      ) : (
        <table className="studio-index-table">
          <SortableHead columns={COLUMNS} sort={sort} onSort={onSort} />
          <tbody>
            {shown.map((row) => (
              <WorkflowRow key={row.workflow.slug} row={row} usageReady={usageReady} />
            ))}
            {unused.length ? (
              <tr className="studio-index-fold">
                <td colSpan={COLUMNS.length}>
                  <Button
                    variant="plain"
                    className="studio-index-fold-toggle"
                    aria-expanded={unusedOpen}
                    onClick={() => setUnusedOpen((open) => !open)}
                  >
                    {unusedOpen ? "▾" : "▸"} {unused.length}{" "}
                    {unused.length === 1 ? "workflow" : "workflows"} no ticket has used
                  </Button>
                </td>
              </tr>
            ) : null}
            {unusedOpen
              ? unused.map((row) => (
                  <WorkflowRow key={row.workflow.slug} row={row} usageReady={usageReady} />
                ))
              : null}
          </tbody>
        </table>
      )}
    </StudioIndexFrame>
  );
}

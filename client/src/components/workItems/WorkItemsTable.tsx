
import type { Approval } from "../../api/chatTypes";
import { duration } from "../../lib/duration";
import type { ActiveRun } from "../../lib/queueSocket";
import { LIVE_RUN_STATUSES } from "../../lib/runningTickets";
import { TICKET_STATE_COLORS, runStatusLabel, stageStatusColor } from "../../lib/ticketStates";
import {
  needsMeReason,
  stageText,
  stateText,
  type WorkItemRow,
  type WorkItemsSort,
  type WorkItemsSortKey,
} from "../../lib/workItemsTable";
import { PrioBars } from "../PrioBars";
import { Button } from "../ui/Button";
import { TicketLink } from "../TicketLink";

interface Column {
  key: WorkItemsSortKey;
  label: string;
}

const COLUMNS: Column[] = [
  { key: "priority", label: "P" },
  { key: "id", label: "ID" },
  { key: "title", label: "Title" },
  { key: "state", label: "State" },
  { key: "stage", label: "Stage" },
  { key: "workspace", label: "Workspace" },
  { key: "parent", label: "Parent" },
];

/**
 * One row per ticket; the title opens it in the Console, the parent opens the
 * parent. `showWhy` adds the reason a ticket is the operator's, for the
 * "Needs me" view, where that reason is the point of the list. A ticket holding
 * an execution slot says so in its stage cell, with how long it has run.
 */
export function WorkItemsTable({
  rows,
  runs,
  sort,
  onSort,
  showWhy,
  approvals,
  onOpenApproval,
}: {
  rows: WorkItemRow[];
  runs: ReadonlyMap<string, ActiveRun>;
  sort: WorkItemsSort;
  onSort: (key: WorkItemsSortKey) => void;
  showWhy: boolean;
  /** Each ticket's pending approval, by ticket id; names what an awaiting row is asked. */
  approvals: ReadonlyMap<string, Approval>;
  /** Opens the inbox on that ticket's approval. */
  onOpenApproval: (ticketId: string) => void;
}) {
  return (
    <table className="wi-table">
      <thead>
        <tr>
          {COLUMNS.map((column) => (
            <th
              key={column.key}
              scope="col"
              className={`wi-col-${column.key}`}
              aria-sort={sort.key === column.key ? sort.dir : "none"}
            >
              <Button
                variant="plain"
                className="wi-sort"
                onClick={() => onSort(column.key)}
                aria-label={column.key === "priority" ? "Priority" : undefined}
              >
                {column.label}
                <span aria-hidden className="wi-sort-mark">
                  {sort.key === column.key ? (sort.dir === "ascending" ? "↑" : "↓") : ""}
                </span>
              </Button>
            </th>
          ))}
          {showWhy ? (
            <th scope="col" className="wi-col-why">
              Why it needs you
            </th>
          ) : null}
        </tr>
      </thead>
      <tbody>
        {rows.map(({ node, parent }) => {
          const run = runs.get(node.id);
          const live = run ? LIVE_RUN_STATUSES.has(run.status) : false;
          return (
          <tr key={node.id}>
            <td className="wi-col-priority" title={`Priority ${node.priority}`}>
              <PrioBars priority={node.priority} />
              <span className="wi-sr">P{node.priority}</span>
            </td>
            <td className="wi-col-id">{node.external_id}</td>
            <td className="wi-col-title">
              <TicketLink className="wi-title" ticketId={node.id} title={node.title}>
                {node.title}
              </TicketLink>
            </td>
            <td className="wi-col-state">
              <span className="wi-dot" style={{ background: TICKET_STATE_COLORS[node.state] }} aria-hidden />
              {stateText(node)}
            </td>
            <td className="wi-col-stage" style={{ color: stageStatusColor(node.workflow_stage_status) }}>
              {stageText(node)}
              {run ? (
                <span className="wi-run" title={`Slot ${run.slot_number}`}>
                  <span className={`wi-run-dot${live ? " is-live" : ""}`} aria-hidden />
                  {live ? `running ${duration(run.elapsed_seconds)}` : runStatusLabel(run.status)}
                </span>
              ) : null}
            </td>
            <td className="wi-col-workspace">{node.workspace_slug ?? ""}</td>
            <td className="wi-col-parent">
              {parent ? (
                <TicketLink className="wi-parent" ticketId={parent.id} title={`${parent.external_id} · ${parent.title}`}>
                  {parent.title}
                </TicketLink>
              ) : null}
            </td>
            {showWhy ? (
              <td className="wi-col-why">
                {approvals.has(node.id) && node.workflow_stage_status === "awaiting" ? (
                  <Button
                    variant="plain"
                    className="wi-why-link"
                    title="Open this approval in the inbox"
                    onClick={() => onOpenApproval(node.id)}
                  >
                    {needsMeReason(node, approvals.get(node.id))}
                  </Button>
                ) : (
                  needsMeReason(node)
                )}
              </td>
            ) : null}
          </tr>
          );
        })}
      </tbody>
    </table>
  );
}

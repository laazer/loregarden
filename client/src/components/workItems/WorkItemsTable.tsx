import { Link } from "react-router-dom";

import { ticketPath } from "../../lib/appNavigation";
import { TICKET_STATE_COLORS, stageStatusColor } from "../../lib/ticketStates";
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
 * "Needs me" view, where that reason is the point of the list.
 */
export function WorkItemsTable({
  rows,
  sort,
  onSort,
  showWhy,
}: {
  rows: WorkItemRow[];
  sort: WorkItemsSort;
  onSort: (key: WorkItemsSortKey) => void;
  showWhy: boolean;
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
        {rows.map(({ node, parent }) => (
          <tr key={node.id}>
            <td className="wi-col-priority" title={`Priority ${node.priority}`}>
              <PrioBars priority={node.priority} />
              <span className="wi-sr">P{node.priority}</span>
            </td>
            <td className="wi-col-id">{node.external_id}</td>
            <td className="wi-col-title">
              <Link className="wi-title" to={ticketPath(node.id)} title={node.title}>
                {node.title}
              </Link>
            </td>
            <td className="wi-col-state">
              <span className="wi-dot" style={{ background: TICKET_STATE_COLORS[node.state] }} aria-hidden />
              {stateText(node)}
            </td>
            <td className="wi-col-stage" style={{ color: stageStatusColor(node.workflow_stage_status) }}>
              {stageText(node)}
            </td>
            <td className="wi-col-workspace">{node.workspace_slug ?? ""}</td>
            <td className="wi-col-parent">
              {parent ? (
                <Link className="wi-parent" to={ticketPath(parent.id)} title={`${parent.external_id} · ${parent.title}`}>
                  {parent.title}
                </Link>
              ) : null}
            </td>
            {showWhy ? <td className="wi-col-why">{needsMeReason(node)}</td> : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

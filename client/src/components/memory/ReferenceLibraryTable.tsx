import { Link } from "react-router-dom";

import type { LibraryRow } from "../../api/referenceLibraryApi";
import { memoryPath } from "../../lib/appNavigation";
import {
  STATE_LABELS,
  licenceUse,
  shortDate,
  type LibrarySort,
  type LibrarySortKey,
} from "../../lib/referenceLibrary";
import { Button } from "../ui/Button";

interface Column {
  key: string;
  label: string;
  sort?: LibrarySortKey;
  numeric?: boolean;
}

const COLUMNS: Column[] = [
  { key: "name", label: "Source", sort: "name" },
  { key: "state", label: "State", sort: "state" },
  { key: "licence", label: "Use" },
  { key: "workspaces", label: "Serves" },
  { key: "passes", label: "Passes", sort: "passes", numeric: true },
  { key: "tickets", label: "Tickets", sort: "tickets", numeric: true },
  { key: "last_read", label: "Last read", sort: "last_read" },
];

/**
 * One row per source. The name opens it; everything else is for comparing.
 * "Serves" appears only when some source serves more than one workspace, since
 * otherwise every row would repeat the workspace already picked above.
 */
export function ReferenceLibraryTable({
  rows,
  sort,
  onSort,
  selectedId,
  search,
  showWorkspaces,
}: {
  rows: LibraryRow[];
  sort: LibrarySort;
  onSort: (key: LibrarySortKey) => void;
  selectedId: string | null;
  search: string;
  showWorkspaces: boolean;
}) {
  const columns = COLUMNS.filter((column) => showWorkspaces || column.key !== "workspaces");
  return (
    <table className="rl-table">
      <thead>
        <tr>
          {columns.map((column) =>
            column.sort ? (
              <th
                key={column.key}
                scope="col"
                className={`rl-col-${column.key}${column.numeric ? " rl-num" : ""}`}
                aria-sort={sort.key === column.sort ? sort.dir : "none"}
              >
                <Button variant="plain" className="rl-sort" onClick={() => onSort(column.sort as LibrarySortKey)}>
                  {column.label}
                  <span aria-hidden className="rl-sort-mark">
                    {sort.key === column.sort ? (sort.dir === "ascending" ? "↑" : "↓") : ""}
                  </span>
                </Button>
              </th>
            ) : (
              <th key={column.key} scope="col" className={`rl-col-${column.key}`}>
                {column.label}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const selected = row.id === selectedId;
          return (
            <tr key={row.id} className={selected ? "rl-row--selected" : undefined}>
              <td className="rl-col-name">
                <Link
                  id={`rl-source-${row.id}`}
                  className="rl-name"
                  to={{ pathname: memoryPath("library", row.id), search }}
                  aria-current={selected ? "true" : undefined}
                >
                  {row.name}
                </Link>
                <span className="rl-url">{row.kind === "site" ? "site · " : ""}{row.url.replace(/^https?:\/\//, "")}</span>
              </td>
              <td className="rl-col-state">
                <span className={`rl-state-badge rl-state-badge--${row.state}`}>{STATE_LABELS[row.state]}</span>
              </td>
              <td className="rl-col-licence" title={row.licence_name || undefined}>
                {licenceUse(row)}
              </td>
              {showWorkspaces ? <td className="rl-col-workspaces">{row.workspaces.join(", ")}</td> : null}
              <td className="rl-col-passes rl-num">{row.passes}</td>
              <td className="rl-col-tickets rl-num">
                {row.tickets}
                {row.unresolved_refs > 0 ? (
                  <span className="rl-unresolved">+{row.unresolved_refs} unmatched</span>
                ) : null}
              </td>
              <td className="rl-col-last_read">{shortDate(row.last_read_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

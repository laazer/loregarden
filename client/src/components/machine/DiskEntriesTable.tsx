import { useMemo, useState } from "react";

import type { DiskNode } from "../../api/diskTypes";
import {
  DISK_KIND_LABELS,
  formatDiskBytes,
  isRegenerable,
  isSelectable,
} from "../../lib/diskUsage";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";

type SortKey = "size" | "name";

interface DiskEntriesTableProps {
  focus: DiskNode;
  rootPaths: ReadonlySet<string>;
  selected: ReadonlySet<string>;
  onToggle: (node: DiskNode) => void;
  onOpen: (node: DiskNode) => void;
  /** The page's find box, applied to names here and highlighted in the map. */
  filter: string;
}

/**
 * The focus's children as rows: the treemap's twin for reading exact sizes,
 * sorting, and choosing what to clean. One primary action per row, on its name
 * (open a folder); the checkbox only marks it for the cleanup review, which
 * decides on the server whether it may go.
 */
export function DiskEntriesTable({
  focus,
  rootPaths,
  selected,
  onToggle,
  onOpen,
  filter,
}: DiskEntriesTableProps) {
  const [sort, setSort] = useState<SortKey>("size");

  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const shown = needle
      ? focus.children.filter((child) =>
          child.name.toLowerCase().includes(needle),
        )
      : focus.children;
    return [...shown].sort((a, b) =>
      sort === "size"
        ? b.size_bytes - a.size_bytes
        : a.name.localeCompare(b.name),
    );
  }, [focus, filter, sort]);

  const sortButton = (key: SortKey, label: string) => (
    <Button variant="plain" className="disk-sort" onClick={() => setSort(key)}>
      {label}
      {sort === key ? (
        <span aria-hidden>{key === "size" ? " ↓" : " ↑"}</span>
      ) : null}
    </Button>
  );

  if (focus.children.length === 0) {
    return (
      <p className="disk-entries-empty">
        Nothing larger than 1 MB is inside {focus.name}. Its{" "}
        {formatDiskBytes(focus.size_bytes)} is spread across small files.
      </p>
    );
  }

  return (
    <div className="disk-entries">
      <div className="disk-entries-scroll">
        <table className="disk-entries-table">
          <caption className="disk-sr-only">Contents of {focus.name}</caption>
          <thead>
            <tr>
              <th scope="col" className="disk-col-select">
                <span className="disk-sr-only">Mark for cleanup</span>
              </th>
              <th
                scope="col"
                aria-sort={sort === "name" ? "ascending" : "none"}
              >
                {sortButton("name", "Name")}
              </th>
              <th scope="col">Kind</th>
              <th
                scope="col"
                className="disk-col-num"
                aria-sort={sort === "size" ? "descending" : "none"}
              >
                {sortButton("size", "Size")}
              </th>
              <th scope="col" className="disk-col-num">
                Share
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="disk-entries-none">
                  No entry matches “{filter}”.
                </td>
              </tr>
            ) : (
              rows.map((node) => {
                const selectable = isSelectable(node, rootPaths);
                const opens = node.is_dir && node.kind !== "other";
                const share = focus.size_bytes
                  ? (node.size_bytes / focus.size_bytes) * 100
                  : 0;
                return (
                  <tr
                    key={`${node.path}:${node.name}`}
                    className={selected.has(node.path) ? "is-selected" : ""}
                  >
                    <td className="disk-col-select">
                      {selectable ? (
                        <Input
                          type="checkbox"
                          checked={selected.has(node.path)}
                          onChange={() => onToggle(node)}
                          aria-label={`Mark ${node.name} for cleanup`}
                        />
                      ) : null}
                    </td>
                    <th scope="row" className="disk-col-name">
                      {opens ? (
                        <Button
                          variant="plain"
                          className="disk-open"
                          onClick={() => onOpen(node)}
                        >
                          {node.name}
                        </Button>
                      ) : (
                        <span className="disk-leaf-name">{node.name}</span>
                      )}
                    </th>
                    <td>
                      <span className={`disk-kind disk-kind--${node.kind}`}>
                        {DISK_KIND_LABELS[node.kind]}
                      </span>
                      {isRegenerable(node) ? (
                        <span className="disk-candidate">regenerable</span>
                      ) : null}
                    </td>
                    <td className="disk-col-num">
                      {formatDiskBytes(node.size_bytes)}
                    </td>
                    <td className="disk-col-num">
                      {share < 0.1 ? "<0.1" : share.toFixed(1)}%
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

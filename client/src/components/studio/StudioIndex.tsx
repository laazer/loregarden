/**
 * What Studio's index pages share: the heading with its counts, a filter and
 * the one create button; a sortable `<table>` head; and the three messages a
 * list needs (it failed, it is empty, nothing matched). Agents and Workflows
 * each decide their own columns, ranking and empty-state words.
 */

import type { ReactNode } from "react";

import type { IndexColumn, SortDirection } from "../../lib/indexSort";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import "./StudioIndex.css";

/** The slice of a react-query result an index reads. */
export interface IndexQuery<T> {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

export function SortableHead<K extends string>({
  columns,
  sort,
  onSort,
}: {
  columns: IndexColumn<K>[];
  sort: { key: K; dir: SortDirection };
  onSort: (column: IndexColumn<K>) => void;
}) {
  return (
    <thead>
      <tr>
        {columns.map((column) => (
          <th
            key={column.key}
            scope="col"
            className={column.numeric ? "num" : undefined}
            aria-sort={sort.key === column.key ? sort.dir : "none"}
          >
            <Button variant="plain" className="studio-index-sort" onClick={() => onSort(column)}>
              {column.label}
              <span aria-hidden className="studio-index-sort-mark">
                {sort.key === column.key ? (sort.dir === "ascending" ? "↑" : "↓") : ""}
              </span>
            </Button>
          </th>
        ))}
      </tr>
    </thead>
  );
}

/** The list itself failed: never drawn as an empty list. */
export function IndexLoadError({ what, query }: { what: string; query: IndexQuery<unknown> }) {
  return (
    <div className="studio-index studio-index--message" role="alert">
      <p>
        Could not load the {what}: {describeError(query.error, "the request failed")}
      </p>
      <Button variant="secondary" onClick={() => void query.refetch()}>
        Try again
      </Button>
    </div>
  );
}

/** A second read (the counts) failed while the list itself loaded. */
export function IndexStatsWarning({ query, children }: { query: IndexQuery<unknown>; children: ReactNode }) {
  if (!query.isError) return null;
  return (
    <div className="studio-index-warning" role="alert">
      <span>
        {children} ({describeError(query.error, "the request failed")})
      </span>
      <Button variant="secondary" compact onClick={() => void query.refetch()}>
        Try again
      </Button>
    </div>
  );
}

export function NoMatch({ noun, query, onClear }: { noun: string; query: string; onClear: () => void }) {
  return (
    <p className="studio-index-empty">
      No {noun} matches “{query}”.{" "}
      <Button variant="plain" className="studio-index-clear" onClick={onClear}>
        Show every {noun}
      </Button>
    </p>
  );
}

export function StudioIndexFrame({
  id,
  title,
  summary,
  noun,
  query,
  onQuery,
  newLabel,
  onNew,
  newOpen,
  children,
}: {
  id: string;
  title: string;
  summary: string;
  /** Singular, for the filter's name: "agent", "workflow". */
  noun: string;
  query: string;
  onQuery: (value: string) => void;
  newLabel: string;
  onNew: () => void;
  newOpen: boolean;
  children: ReactNode;
}) {
  return (
    <section className="studio-index" aria-labelledby={id}>
      <header className="studio-index-head">
        <div>
          <h2 id={id} className="studio-index-title">
            {title}
          </h2>
          <p className="studio-index-summary">{summary}</p>
        </div>
        <Input
          type="search"
          className="studio-index-filter"
          aria-label={`Filter ${noun}s`}
          placeholder={`Filter ${noun}s`}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
        <Button variant="primary" onClick={onNew} aria-expanded={newOpen}>
          {newLabel}
        </Button>
      </header>
      {children}
    </section>
  );
}

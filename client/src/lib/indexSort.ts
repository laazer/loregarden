import { useState } from "react";

/** How a Studio index column is sorted; the value `aria-sort` takes. */
export type SortDirection = "ascending" | "descending";

export interface IndexColumn<K extends string> {
  key: K;
  label: string;
  numeric: boolean;
  /** The direction a first click sorts: most or worst first, for numbers. */
  first: SortDirection;
}

export function useIndexSort<K extends string>(initial: { key: K; dir: SortDirection }) {
  const [sort, setSort] = useState(initial);
  const onSort = (column: IndexColumn<K>) =>
    setSort((current) =>
      current.key === column.key
        ? { key: column.key, dir: current.dir === "ascending" ? "descending" : "ascending" }
        : { key: column.key, dir: column.first },
    );
  return { sort, onSort };
}

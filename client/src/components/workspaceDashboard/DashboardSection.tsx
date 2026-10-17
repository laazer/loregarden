import type { ReactNode } from "react";

import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";

export interface SectionQuery {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  refetch: () => unknown;
}

interface DashboardSectionProps {
  id: string;
  title: string;
  /** Shown beside the title, e.g. "8 open". Omitted while the count is unknown. */
  count?: string;
  /** The section's own controls (a filter, a link), right of the title. */
  actions?: ReactNode;
  /** The read(s) this section renders; their loading and error states are drawn here. */
  queries: SectionQuery[];
  /** What failed, for the error line: "Could not list pull requests". */
  errorTitle: string;
  /** True when the data loaded and there is nothing to show. */
  empty: boolean;
  /** What would be here, and how to get one. Must not read like an error. */
  emptyMessage: ReactNode;
  skeletonRows?: number;
  children: ReactNode;
}

/**
 * One dashboard section and its five states.
 *
 * Every section reads its own endpoint, so one slow or failing read (GitHub,
 * typically) leaves the rest of the page usable. A failure is drawn in place,
 * naming what failed with a Retry, rather than as a toast that is gone before
 * the operator scrolls to the empty box it explains.
 */
export function DashboardSection({
  id,
  title,
  count,
  actions,
  queries,
  errorTitle,
  empty,
  emptyMessage,
  skeletonRows = 4,
  children,
}: DashboardSectionProps) {
  const failed = queries.find((query) => query.isError);
  const pending = queries.some((query) => query.isPending);
  const headingId = `${id}-heading`;

  let body: ReactNode;
  if (failed) {
    body = (
      <div className="wsd-section-error" role="alert">
        <p>
          {errorTitle}: {describeError(failed.error)}
        </p>
        <Button variant="secondary" compact disabled={failed.isFetching} onClick={() => void failed.refetch()}>
          {failed.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    );
  } else if (pending) {
    body = <PaneSkeleton variant="list" rows={skeletonRows} label={`Loading ${title.toLowerCase()}…`} />;
  } else if (empty) {
    body = <p className="wsd-section-empty">{emptyMessage}</p>;
  } else {
    body = children;
  }

  return (
    <section className="wsd-section" aria-labelledby={headingId}>
      <header className="wsd-section-header">
        <h2 id={headingId}>
          {title}
          {count !== undefined && !pending && !failed ? <span className="wsd-section-count"> {count}</span> : null}
        </h2>
        {actions ? <div className="wsd-section-actions">{actions}</div> : null}
      </header>
      {body}
    </section>
  );
}

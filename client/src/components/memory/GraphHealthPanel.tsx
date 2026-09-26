/**
 * The memory graph's shape, against the last recorded snapshot.
 *
 * Shares of live learnings, never raw counts: a count rises whether the graph
 * is improving or not. One reading says little, so the panel always shows the
 * previous snapshot beside it and says in words what moved — and names one
 * share to watch, not five.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { HEALTH_SHARES, type GraphHealthReport } from "../../api/memoryApi";
import { HEALTH_SHARE_LABELS } from "../../lib/memoryHealth";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { describeError, pushToast } from "../../state/toastStore";
import { PaneSkeleton } from "../ui/PaneSkeleton";

function Shares({ report }: { report: GraphHealthReport }) {
  const { current, previous, watch } = report;
  return (
    <dl className="memory-shares">
      {HEALTH_SHARES.map((share) => {
        const now = current.shares[share];
        const was = previous?.shares[share];
        const delta = was === undefined ? null : Math.round((now - was) * 10) / 10;
        return (
          <div
            key={share}
            className={`memory-share${share === watch ? " memory-share--watch" : ""}`}
          >
            <dt title={HEALTH_SHARE_LABELS[share].hint}>
              {HEALTH_SHARE_LABELS[share].label}
              {share === watch ? " · watch" : ""}
            </dt>
            <dd>
              <span className="memory-share-value">{now}%</span>
              {delta !== null && delta !== 0 && (
                <span className={delta > 0 ? "memory-share-up" : "memory-share-down"}>
                  {delta > 0 ? "+" : ""}
                  {delta} pts
                </span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function GraphHealthPanel({ workspaceSlug }: { workspaceSlug: string }) {
  const qc = useQueryClient();
  const report = useQuery({
    queryKey: ["memory-graph-health", workspaceSlug],
    queryFn: () => api.memoryGraphHealth(workspaceSlug),
    meta: { errorTitle: "Load graph health" },
  });
  const record = useMutation({
    meta: { errorTitle: "Record graph snapshot" },
    mutationFn: () => api.recordMemoryGraphHealth(workspaceSlug),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["memory-graph-health", workspaceSlug] });
      pushToast({ tone: "success", title: "Snapshot recorded", message: workspaceSlug });
    },
  });

  let body;
  if (report.isLoading) {
    body = <PaneSkeleton variant="list" rows={5} label="Measuring the graph…" />;
  } else if (!report.data) {
    body = (
      <p className="memory-error" role="alert">
        Could not measure the graph: {describeError(report.error, "the request failed")}.
      </p>
    );
  } else if (report.data.current.figures.learnings === 0) {
    body = (
      <p className="memory-empty">
        No live learnings in {workspaceSlug} yet, so there is no shape to measure.
      </p>
    );
  } else {
    const { current, previous, notes } = report.data;
    body = (
      <>
        <p className="memory-muted">
          {current.figures.learnings} live learnings · {current.figures.superseded} superseded ·{" "}
          {current.figures.discredited} discredited
          {previous
            ? ` · compared with the snapshot of ${formatLocalTimestamp(previous.measured_at)}`
            : ""}
        </p>
        <Shares report={report.data} />
        {notes.map((note) => (
          <p key={note} className="memory-note" role="status">
            {note}
          </p>
        ))}
      </>
    );
  }

  return (
    <section className="memory-panel" aria-labelledby="memory-graph-health-title">
      <header className="memory-panel-header">
        <h2 id="memory-graph-health-title" className="memory-panel-title">
          Graph health
        </h2>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={record.isPending || !report.data}
          onClick={() => record.mutate()}
        >
          {record.isPending ? "Recording…" : "Record snapshot"}
        </button>
      </header>
      {body}
    </section>
  );
}

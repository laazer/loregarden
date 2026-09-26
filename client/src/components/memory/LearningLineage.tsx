/**
 * What this position replaced and what replaced it, oldest first — each step
 * with the change notes its own history recorded. A correction (a discredit)
 * and a revision (a supersession) read differently on purpose: one says the
 * learning was wrong, the other that it stopped being true.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { PaneSkeleton } from "../ui/PaneSkeleton";

export function LearningLineage({
  nodeId,
  workspaceSlug,
}: {
  nodeId: string;
  workspaceSlug: string;
}) {
  const lineage = useQuery({
    queryKey: ["memory-lineage", workspaceSlug, nodeId],
    queryFn: () => api.memoryLineage(nodeId, workspaceSlug),
    meta: { errorTitle: "Load learning lineage" },
  });

  if (lineage.isLoading) return <PaneSkeleton variant="list" rows={2} label="Loading lineage…" />;
  if (!lineage.data) {
    return (
      <p className="memory-error" role="alert">
        Could not load the lineage: {describeError(lineage.error, "the request failed")}.
      </p>
    );
  }
  const { steps } = lineage.data;
  if (steps.length < 2) {
    return <p className="memory-muted">Nothing replaced this learning, and it replaced nothing.</p>;
  }
  return (
    <ol className="memory-lineage" aria-label="Lineage, oldest first">
      {steps.map((step, index) => {
        const note = step.versions.at(-1)?.change_note;
        return (
          <li key={step.id} aria-current={step.id === nodeId ? "true" : undefined}>
            <strong>{step.title}</strong>
            <span className="memory-muted">
              {" "}
              · written {formatLocalTimestamp(step.created_at)}
              {step.discredited ? " · withdrawn (recorded wrong)" : ""}
              {index < steps.length - 1 && !step.discredited
                ? " · superseded (stopped being true)"
                : ""}
            </span>
            {note ? <p className="memory-muted">Last change: {note}</p> : null}
          </li>
        );
      })}
    </ol>
  );
}

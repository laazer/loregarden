import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { ResearchRunDiff } from "../../../api/referenceResearchApi";
import { researchDiffKey } from "../../../lib/researchCandidates";
import { describeError } from "../../../state/toastStore";
import { ResearchDiffView } from "../../research/ResearchDiffView";
import { PrimitiveCard } from "./PrimitiveCard";
import type { ResearchDiffPart } from "./types";

function diffLine(diff: ResearchRunDiff): string {
  if (diff.previous_run === 0) return `Run 1 · ${diff.added.length} put forward`;
  return [
    `Run ${diff.run} vs ${diff.previous_run}`,
    `${diff.added.length} new`,
    diff.dropped_known ? `${diff.dropped.length} gone` : "",
    `${diff.changed.length} changed`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** A re-run's answer: what changed since the run before, read from the record by question id. */
export function ResearchDiffPrimitive({ part }: { part: ResearchDiffPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: researchDiffKey(part.question_id, part.run),
    queryFn: () => api.researchRunDiff(part.question_id, part.run),
  });
  return (
    <PrimitiveCard
      kind="research_diff"
      title={part.title ?? data?.question ?? "Research changes"}
      refLine={data ? diffLine(data) : null}
      loading={isLoading}
      skeletonLines={3}
      error={error ? `Couldn't load what this run changed: ${describeError(error, "request failed")}` : null}
    >
      {data ? <ResearchDiffView diff={data} /> : null}
    </PrimitiveCard>
  );
}

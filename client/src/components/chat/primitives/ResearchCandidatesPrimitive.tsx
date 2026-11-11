import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { fullyVerified, researchQuestionKey } from "../../../lib/researchCandidates";
import { describeError } from "../../../state/toastStore";
import { ResearchCandidatesTable } from "../../research/ResearchCandidatesTable";
import { PrimitiveCard } from "./PrimitiveCard";
import type { ResearchCandidatesPart } from "./types";

/**
 * A research thread's answer: the candidates and claims as recorded, read by id.
 * The part carries no rows on purpose, so a claim the server refused can never
 * appear here just because the agent typed it.
 */
export function ResearchCandidatesPrimitive({ part }: { part: ResearchCandidatesPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: researchQuestionKey(part.question_id),
    queryFn: () => api.researchQuestion(part.question_id),
  });
  const count = data?.candidates.length ?? 0;
  const verified = data ? fullyVerified(data.candidates, data.capabilities) : 0;
  return (
    <PrimitiveCard
      kind="research_candidates"
      title={part.title ?? data?.question ?? "Research"}
      refLine={
        data
          ? `${count} candidate${count === 1 ? "" : "s"} · ${verified} verified for everything asked · run ${data.run_count}`
          : null
      }
      loading={isLoading}
      skeletonLines={4}
      error={error ? `Couldn't load this research: ${describeError(error, "request failed")}` : null}
    >
      {data ? (
        count ? (
          <ResearchCandidatesTable question={data} />
        ) : (
          <p className="rs-empty">
            No candidates recorded for this question yet. Ask Baxter to look again, or widen the
            question.
          </p>
        )
      ) : null}
    </PrimitiveCard>
  );
}

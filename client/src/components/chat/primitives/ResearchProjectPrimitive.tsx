import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { researchProjectKey } from "../../../lib/researchCandidates";
import { describeError } from "../../../state/toastStore";
import { ResearchProjectView } from "../../research/ResearchProjectView";
import { PrimitiveCard } from "./PrimitiveCard";
import type { ResearchProjectPart } from "./types";

/** "What do we know about X?", answered from the record across every research question. */
export function ResearchProjectPrimitive({ part }: { part: ResearchProjectPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: researchProjectKey(part.url),
    queryFn: () => api.researchProject(part.url),
  });
  const verified = data?.claims.filter((claim) => claim.status === "verified").length ?? 0;
  return (
    <PrimitiveCard
      kind="research_project"
      title={part.title ?? (data ? data.name || data.url : "Research project")}
      refLine={
        data
          ? `${data.claims.length} claim${data.claims.length === 1 ? "" : "s"} · ${verified} verified · ${data.questions.length} question${data.questions.length === 1 ? "" : "s"}`
          : null
      }
      loading={isLoading}
      skeletonLines={3}
      error={error ? `Couldn't load this project: ${describeError(error, "request failed")}` : null}
    >
      {data ? <ResearchProjectView project={data} /> : null}
    </PrimitiveCard>
  );
}

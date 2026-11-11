import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { CLAIM_LABELS, researchClaimKey } from "../../../lib/researchCandidates";
import { describeError } from "../../../state/toastStore";
import { ResearchClaimView } from "../../research/ResearchClaimView";
import { PrimitiveCard } from "./PrimitiveCard";
import type { ResearchClaimPart } from "./types";

/**
 * "Can X do Y?", answered from the record: the claim that stands now for one
 * project and capability, read by url + capability so a later refutation shows.
 */
export function ResearchClaimPrimitive({ part }: { part: ResearchClaimPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: researchClaimKey(part.url, part.capability),
    queryFn: () => api.researchClaim(part.url, part.capability),
  });
  return (
    <PrimitiveCard
      kind="research_claim"
      title={part.title ?? (data ? `${data.name || data.url}: ${data.capability}` : "Research claim")}
      refLine={data ? (data.claim ? CLAIM_LABELS[data.claim.status] : "Not checked") : null}
      loading={isLoading}
      skeletonLines={3}
      error={error ? `Couldn't load this claim: ${describeError(error, "request failed")}` : null}
    >
      {data ? <ResearchClaimView card={data} /> : null}
    </PrimitiveCard>
  );
}

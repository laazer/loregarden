import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { mergeApprovals } from "../lib/approvals";

/**
 * A ticket's triage snapshot and its pending approvals, polled together.
 *
 * The two queries and their polling rules were copied verbatim across the
 * triage panel, the logs panel and the dashboard, so a change to how often a
 * busy ticket refreshes had to be made in three places to hold.
 *
 * `idleIntervalMs` is a parameter rather than a constant because the surfaces
 * genuinely differ: the panels poll an idle ticket every 5s, the dashboard
 * every 8s. Collapsing them to one number would be a behaviour change wearing
 * a refactor's clothes.
 */
export function useTriageSession(ticketId: string | undefined, idleIntervalMs = 5000) {
  const triage = useQuery({
    queryKey: ["triage", ticketId],
    queryFn: () => api.triage(ticketId!),
    enabled: !!ticketId,
    retry: 1,
    refetchInterval: (query) => {
      const pending = query.state.data?.pending_approvals?.length ?? 0;
      const busy = query.state.data ? query.state.data.run_status !== "idle" : false;
      return pending > 0 || busy ? 2000 : idleIntervalMs;
    },
  });

  const approvals = useQuery({
    queryKey: ["approvals", ticketId],
    queryFn: () => api.approvals(ticketId!),
    enabled: !!ticketId,
    refetchInterval: 2000,
  });

  return {
    triage,
    approvals,
    /** Both sources, deduped — an approval raised mid-run appears in each. */
    pending: mergeApprovals(triage.data?.pending_approvals, approvals.data),
    /**
     * Server-derived, never promise-derived: a turn outlives the request that
     * started it, so a locally-tracked flag would clear while the agent is
     * still working.
     */
    isBusy: triage.data ? triage.data.run_status !== "idle" : false,
  };
}

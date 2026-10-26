import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useUiStore } from "../state/uiStore";

/**
 * Board-wide ticket counts for the chosen workspace, or every workspace while
 * none is chosen.
 *
 * One query key for every reader: Home's status card and the rail's counts
 * share the cache, so the rail costs no request on Home and the two can never
 * show different numbers.
 */
export function useTicketStatusSummary() {
  const workspace = useUiStore((s) => s.workspace);
  const workspaceParam = workspace && workspace !== "all" ? workspace : undefined;
  return useQuery({
    queryKey: ["home-status-summary", workspaceParam],
    queryFn: () => api.ticketStatusSummary(workspaceParam),
    // Faster than the lists: this is the card people watch to see whether
    // anything is actually moving.
    refetchInterval: 10_000,
    meta: { errorTitle: "Load ticket counts" },
  });
}

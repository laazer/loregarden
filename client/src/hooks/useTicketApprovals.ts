import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApprovalResolutionAction } from "../api/chatTypes";
import { api } from "../api/client";
import type { ApprovalResolvePayload } from "../components/ApprovalCard";

/**
 * The pending approvals on a ticket's subtree, polled. One query key, so the
 * Approvals tab and the ticket modal share the cache and a resolve in either
 * clears both.
 */
export function useTicketApprovals(ticketId: string | undefined) {
  return useQuery({
    queryKey: ["approvals", ticketId],
    queryFn: () => api.approvals(ticketId),
    refetchInterval: 5000,
    enabled: !!ticketId,
  });
}

/** Approve, recheck, reject or ignore one approval, then refresh what shows it. */
export function useResolveApproval() {
  const qc = useQueryClient();
  return useMutation({
    meta: { errorTitle: "Resolve approval" },
    mutationFn: ({
      id,
      action,
      payload,
    }: {
      id: string;
      action: ApprovalResolutionAction;
      payload?: ApprovalResolvePayload;
    }) => api.resolveApproval(id, { action, ...payload }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["approval-history"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });
}

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type Approval } from "../api/client";
import type { ApprovalResolvePayload } from "../components/ApprovalCard";

export interface ResolveApprovalVariables extends ApprovalResolvePayload {
  id: string;
  action: "approve" | "reject";
}

/**
 * Resolving an approval, wherever the card is shown.
 *
 * Two copies of this existed and had drifted: the triage panel forwarded
 * `route_to_stage_key`, the logs panel did not — so rejecting a gate with an
 * explicit route worked from one tab and silently fell back to the default
 * target from the other. The card offers the choice in both places, which is
 * what made the difference invisible.
 *
 * Taking the whole payload through rather than naming each field keeps a new
 * card option from having to be threaded through here a second time.
 */
export function useApprovalResolution(
  ticketId: string | undefined,
  onResolved?: () => void,
) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: ({ id, action, ...payload }: ResolveApprovalVariables) =>
      api.resolveApproval(id, { action, ...payload }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["triage", ticketId] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["runs", ticketId] });
      onResolved?.();
    },
  });
}

/** Convenience for the call sites, which all resolve a whole `Approval`. */
export function approvalVariables(
  approval: Approval,
  action: "approve" | "reject",
  payload?: ApprovalResolvePayload,
): ResolveApprovalVariables {
  return { id: approval.id, action, ...payload };
}

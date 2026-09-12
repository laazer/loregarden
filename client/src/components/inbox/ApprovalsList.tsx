/**
 * The pending approvals, as a list that belongs to no particular surface.
 *
 * ## Why it moved out of the drawer
 *
 * `ApprovalInboxPanel` was on `excludedPanels` for a true reason: it reads the
 * singleton `uiStore` and renders into a fixed drawer, so two of them would
 * fight over one open flag and one position. None of that is true of the
 * *approvals* inside it — a query, a resolve mutation, and a list of cards — and
 * that half is what an operator actually wants beside a run log.
 *
 * So the drawer keeps what is genuinely singular (the open flag, the overlay,
 * the notification log, and the navigation out of it) and this keeps what is
 * not. The drawer is now one caller of two.
 *
 * ## What it deliberately does not do
 *
 * It reads no `state/` store and no router. That is the rule container
 * primitives are held to — `containerPrimitives.render.test` asserts it by
 * scanning imports, because a zustand store read outside a provider returns a
 * value instead of throwing, so nothing else would catch the coupling — and a
 * module a primitive mounts has to keep it or the scan is checking one file
 * while the violation sits in the next one.
 *
 * Navigating to a ticket is therefore a prop, not an import. The drawer passes
 * one because it is page chrome with a route under it; the pane passes none,
 * and the affordance is simply absent there rather than present and broken.
 *
 * ## Workspace scope
 *
 * `/api/inbox/approvals` takes a ticket filter and no workspace filter, but
 * every approval carries its `workspace_slug`, so the narrowing happens here.
 * `""` means every workspace, which is what the drawer wants: an approval is a
 * thing that is *blocking a run*, and hiding one because the sidebar is pointed
 * elsewhere is how a run sits parked for an afternoon.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, type Approval } from "../../api/client";
import { hasHumanCriteria } from "../../utils/approvalCriteria";
import { formatApprovalResolveError } from "../../utils/approvalErrors";
import { ApprovalCard, type ApprovalResolvePayload } from "../ApprovalCard";
import { ApprovalDetailModal } from "../ApprovalDetailModal";

export interface ApprovalsListProps {
  /**
   * Narrow to one workspace, or `""` for every one.
   *
   * Not optional: "I did not think about scope" and "I want them all" are the
   * same value here, and a caller that has to write `""` has decided.
   */
  workspaceSlug: string;
  /**
   * Whether to poll. A closed drawer and an unmounted pane should not be asking
   * the server every five seconds on a machine running a dozen agents.
   */
  isActive: boolean;
  /**
   * Open the ticket an approval belongs to. Omitted where there is no route to
   * open it into, which removes the button rather than leaving it inert.
   */
  onInspect?: (approval: Approval) => void;
}

export function ApprovalsList({ workspaceSlug, isActive, onInspect }: ApprovalsListProps) {
  const qc = useQueryClient();
  const [expandedApproval, setExpandedApproval] = useState<Approval | null>(null);

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
    enabled: isActive,
  });

  const resolveApproval = useMutation({
    meta: { errorTitle: "Resolve approval" },
    mutationFn: ({
      id,
      action,
      answers,
      response,
      always_allow,
      allow_for_ticket,
      allow_for_stage,
      route_to_stage_key,
    }: {
      id: string;
      action: "approve" | "reject";
      answers?: Record<string, string | string[]>;
      response?: string;
      always_allow?: boolean;
      allow_for_ticket?: boolean;
      allow_for_stage?: boolean;
      route_to_stage_key?: string;
    }) =>
      api.resolveApproval(id, {
        action,
        answers,
        response,
        always_allow,
        allow_for_ticket,
        allow_for_stage,
        route_to_stage_key,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
      setExpandedApproval(null);
    },
  });

  // Gates first. The rail rendered whatever order the API returned, so a stage
  // sign-off could sit below a stack of permission prompts — and the inbox data
  // says which deserves the top: permission prompts are rejected 0.4% of the
  // time, gates 13.3% (lg-workflow-integrity-107). Stable, so ordering within
  // each group is unchanged.
  //
  // The workspace filter runs before the sort rather than after: sorting a list
  // and then removing rows from it is the same answer for more work.
  const orderedApprovals = useMemo(() => {
    const scoped = (approvals.data ?? []).filter(
      (approval) => workspaceSlug === "" || approval.workspace_slug === workspaceSlug,
    );
    return scoped.sort((a, b) => Number(hasHumanCriteria(b)) - Number(hasHumanCriteria(a)));
  }, [approvals.data, workspaceSlug]);

  const inspect = (approval: Approval) => {
    if (onInspect === undefined) return;
    setExpandedApproval(null);
    onInspect(approval);
  };

  return (
    <>
      {resolveApproval.isError && (
        <div
          style={{
            fontSize: 11.5,
            color: "var(--rdl)",
            marginBottom: 12,
            padding: "8px 10px",
            borderRadius: 8,
            background: "rgba(240,96,63,.08)",
            border: "1px solid rgba(240,96,63,.25)",
          }}
        >
          {formatApprovalResolveError(resolveApproval.error)}
        </div>
      )}
      {orderedApprovals.map((a) => (
        <ApprovalCard
          key={a.id}
          approval={a}
          onApprove={(payload) =>
            resolveApproval.mutate({ id: a.id, action: "approve", ...payload })
          }
          onReject={(payload) => resolveApproval.mutate({ id: a.id, action: "reject", ...payload })}
          onInspect={onInspect !== undefined && a.ticket_id ? () => inspect(a) : undefined}
          inspectLabel={hasHumanCriteria(a) ? "Approvals tab" : "Inspect"}
          collapsible
          onExpand={() => setExpandedApproval(a)}
          isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === a.id}
        />
      ))}
      {orderedApprovals.length === 0 ? (
        <div className="inbox-empty-hint">
          {/* Two empties, kept apart: nothing is waiting anywhere, versus
              nothing is waiting *here*. The second reads as a broken filter
              unless it names the workspace it filtered to. */}
          {workspaceSlug === ""
            ? "No pending approvals"
            : `No pending approvals in ${workspaceSlug}`}
        </div>
      ) : null}
      <ApprovalDetailModal
        open={!!expandedApproval}
        approval={expandedApproval}
        isSubmitting={
          resolveApproval.isPending && resolveApproval.variables?.id === expandedApproval?.id
        }
        onClose={() => setExpandedApproval(null)}
        onApprove={(payload?: ApprovalResolvePayload) => {
          if (!expandedApproval) return;
          resolveApproval.mutate({ id: expandedApproval.id, action: "approve", ...payload });
        }}
        onReject={(payload?: ApprovalResolvePayload) => {
          if (!expandedApproval) return;
          resolveApproval.mutate({ id: expandedApproval.id, action: "reject", ...payload });
        }}
        onOpenApprovalsTab={
          onInspect !== undefined && expandedApproval?.ticket_id
            ? () => inspect(expandedApproval)
            : undefined
        }
      />
    </>
  );
}

/** How many approvals a caller's own header should count. Shares the query. */
export function usePendingApprovalCount(workspaceSlug: string, isActive: boolean): number {
  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
    enabled: isActive,
  });
  return (approvals.data ?? []).filter(
    (approval) => workspaceSlug === "" || approval.workspace_slug === workspaceSlug,
  ).length;
}

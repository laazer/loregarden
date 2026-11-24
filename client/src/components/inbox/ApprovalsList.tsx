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
 * It reads no store and no router. That is the rule container
 * primitives are held to — `containerPrimitives.render.test` asserts it by
 * scanning imports, because a zustand store read outside a provider returns a
 * value instead of throwing, so nothing else would catch the coupling — and a
 * module a primitive mounts has to keep it or the scan is checking one file
 * while the violation sits in the next one. `describeError` is imported from
 * `state/toastStore`, which is a module that happens to hold a store and not a
 * read of one: the helper is pure, and the scan's own list agrees.
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

import type { ApprovalResolutionAction } from "../../api/chatTypes";
import { api, type Approval } from "../../api/client";
import { groupApprovalsByWorkspace, inWorkspaceScope } from "../../lib/approvals";
import { duplicateApprovalIds } from "../../lib/ignoreReason";
import { describeError } from "../../state/toastStore";
import { formatApprovalResolveError } from "../../utils/approvalErrors";
import { ApprovalCard, type ApprovalResolvePayload } from "../ApprovalCard";
import { ApprovalDetailModal } from "../ApprovalDetailModal";
import { InboxSection } from "./InboxSection";

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
  /** Open a stage gate on the review page. Omitted, as above, where there is no route. */
  onOpenReview?: (approval: Approval) => void;
  /**
   * The workspace groups folded away, and how to fold one. Omitted where nothing
   * keeps the choice, which leaves the headings plain.
   */
  groupFolding?: {
    isCollapsed: (workspaceSlug: string) => boolean;
    onToggle: (workspaceSlug: string) => void;
  };
}

/**
 * A failure with nothing behind it: the request never landed, so there is no
 * answer to show and "nothing is waiting" would be a guess.
 *
 * Written on `errorUpdateCount` rather than `isError` on purpose, and the two
 * are not interchangeable here. `ApprovalsList` is a second observer of
 * `["approvals"]`, and mounting an observer on an errored query refetches it;
 * in TanStack v5 a fetch over a query holding no data resets `status` to
 * `pending` and clears `error`. So `isError && data === undefined` is false the
 * instant the mount it caused begins — the drawer's branch would unmount the
 * component whose mount falsified its own condition, and then remount it when
 * the fetch rejects. Measured at 269 fetches in 300ms against a one-fetch
 * control. `errorUpdateCount` is not cleared by a starting refetch, so the
 * predicate survives it and the branch rests.
 *
 * `data === undefined` is the other half, and it is the no-response/response-of-
 * none boundary: a cached `[]` is a real answer, and a failed poll on top of it
 * must not turn "nothing is waiting" into "could not load".
 */
function isColdFailure(query: { data: unknown; errorUpdateCount: number }): boolean {
  return query.data === undefined && query.errorUpdateCount > 0;
}

export function ApprovalsList({
  workspaceSlug,
  isActive,
  onInspect,
  onOpenReview,
  groupFolding,
}: ApprovalsListProps) {
  const qc = useQueryClient();
  const [expandedApproval, setExpandedApproval] = useState<Approval | null>(null);

  const approvals = useQuery({
    // One `meta` per query key, last observer to set options winning — so every
    // observer of ["approvals"] names the same thing as having failed, or the
    // cold-cache toast says "Load" and nothing else.
    meta: { errorTitle: "Load approvals" },
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
      action: ApprovalResolutionAction;
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

  // Scoped, gates first, then one group per workspace — the whole ordering
  // rule is `lib/approvals`, which is testable without a renderer. Recomputed
  // from the payload on every poll, so a workspace whose last card was just
  // resolved has no heading left behind.
  const groups = useMemo(
    () => groupApprovalsByWorkspace(approvals.data ?? [], workspaceSlug),
    [approvals.data, workspaceSlug],
  );
  // The drawer groups; a caller that already chose a workspace would only get
  // a heading naming the choice it made.
  const grouped = workspaceSlug === "";
  // A failed poll over data the operator can still act on is not a reason to
  // blank the drawer; only a failure with nothing behind it replaces the list.
  const coldFailure = isColdFailure(approvals);
  // Undefined means no response yet, which is not the same as none pending.
  // `coldFailure` already implies it, so one test covers both.
  const showEmptyHint = approvals.data !== undefined && groups.length === 0;
  // Across every workspace, not just the filtered ones: a duplicate is the same
  // card twice, wherever the second one is listed.
  const duplicates = useMemo(() => duplicateApprovalIds(approvals.data ?? []), [approvals.data]);

  const inspect = (approval: Approval) => {
    if (onInspect === undefined) return;
    setExpandedApproval(null);
    onInspect(approval);
  };

  const openReview = (approval: Approval) => {
    if (onOpenReview === undefined) return;
    setExpandedApproval(null);
    onOpenReview(approval);
  };

  // One card, wherever it is rendered. Keyed by the approval's id and matched
  // to the in-flight mutation by the same id — never by position, which
  // grouping no longer agrees with.
  const renderCard = (a: Approval) => (
    <ApprovalCard
      key={a.id}
      approval={a}
      onApprove={(payload) => resolveApproval.mutate({ id: a.id, action: "approve", ...payload })}
      onRecheck={(payload) => resolveApproval.mutate({ id: a.id, action: "recheck", ...payload })}
      onReject={(payload) => resolveApproval.mutate({ id: a.id, action: "reject", ...payload })}
      onInspect={onInspect !== undefined && a.ticket_id ? () => inspect(a) : undefined}
      onOpenReview={onOpenReview !== undefined ? () => openReview(a) : undefined}
      onIgnore={(reason) => resolveApproval.mutate({ id: a.id, action: "ignore", response: reason })}
      hasDuplicate={duplicates.has(a.id)}
      collapsible
      onExpand={() => setExpandedApproval(a)}
      isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === a.id}
      // Suppressed only while grouped: the heading above already names the
      // workspace. Omitted otherwise, so the scoped surface keeps the default.
      {...(grouped ? { showWorkspace: false } : {})}
    />
  );

  return (
    <>
      {resolveApproval.isError && (
        <div className="inbox-error-line">
          {formatApprovalResolveError(resolveApproval.error)}
        </div>
      )}
      {coldFailure && (
        /* Where the cards would be, and instead of the empty hint: before this
           branch existed, a server that was down read as "nothing is waiting". */
        <div className="inbox-error-line">
          {describeError(approvals.error, "Could not load approvals")}
        </div>
      )}
      {grouped
        ? groups.map((group) => (
            <InboxSection
              // Keyed by the slug, not the index, so a group that changes
              // position re-parents none of its cards.
              key={group.workspaceSlug}
              heading="h3"
              title={group.workspaceSlug}
              label={`${group.workspaceSlug} — ${group.approvals.length} pending approval${
                group.approvals.length === 1 ? "" : "s"
              }`}
              count={group.approvals.length}
              collapsed={groupFolding?.isCollapsed(group.workspaceSlug)}
              onToggle={groupFolding ? () => groupFolding.onToggle(group.workspaceSlug) : undefined}
            >
              {group.approvals.map(renderCard)}
            </InboxSection>
          ))
        : groups.flatMap((group) => group.approvals.map(renderCard))}
      {showEmptyHint ? (
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
        onRecheck={(payload?: ApprovalResolvePayload) => {
          if (!expandedApproval) return;
          resolveApproval.mutate({ id: expandedApproval.id, action: "recheck", ...payload });
        }}
        onReject={(payload?: ApprovalResolvePayload) => {
          if (!expandedApproval) return;
          resolveApproval.mutate({ id: expandedApproval.id, action: "reject", ...payload });
        }}
        onIgnore={(reason) => {
          if (!expandedApproval) return;
          resolveApproval.mutate({ id: expandedApproval.id, action: "ignore", response: reason });
        }}
        hasDuplicate={expandedApproval ? duplicates.has(expandedApproval.id) : false}
        onOpenApprovalsTab={
          onInspect !== undefined && expandedApproval?.ticket_id
            ? () => inspect(expandedApproval)
            : undefined
        }
      />
    </>
  );
}

/** What a caller's own header needs to know about the shared approvals query. */
export interface PendingApprovalCount {
  /** How many are pending in scope. `0` during a cold failure: nothing is known to be waiting. */
  count: number;
  /**
   * The query failed with nothing cached. A caller that treats `count === 0` as
   * "inbox zero" needs this, or a server that is down reads as an empty inbox.
   * The same predicate the list's error line is written on, so the drawer's
   * branch and the line it mounts cannot disagree.
   */
  coldFailure: boolean;
}

/** How many approvals a caller's own header should count. Shares the query. */
export function usePendingApprovalCount(
  workspaceSlug: string,
  isActive: boolean,
): PendingApprovalCount {
  const approvals = useQuery({
    // One `meta` per query key, last observer to set options winning — so every
    // observer of ["approvals"] names the same thing as having failed, or the
    // cold-cache toast says "Load" and nothing else.
    meta: { errorTitle: "Load approvals" },
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
    enabled: isActive,
  });
  return {
    count: (approvals.data ?? []).filter((approval) => inWorkspaceScope(approval, workspaceSlug))
      .length,
    coldFailure: isColdFailure(approvals),
  };
}

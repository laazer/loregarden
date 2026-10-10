import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Approval, type TicketDetail } from "../../api/client";
import {
  checklistCoversCriteria,
  hasHumanCriteria,
  impactWithoutCriteria,
} from "../../utils/approvalCriteria";
import { describeError } from "../../state/toastStore";
import { formatApprovalResolveError } from "../../utils/approvalErrors";
import { navigateToTicketTab } from "../../lib/useAppNavigation";
import { ApprovalCard, type ApprovalResolvePayload } from "../ApprovalCard";
import { MarkdownContent } from "../chat/MarkdownContent";
import { Button } from "../ui/Button";
import { ApprovalHistoryList } from "./ApprovalHistoryList";

function kindLabel(approval: Approval): string {
  if (approval.kind === "workflow_gate") return "Stage sign-off";
  if (approval.kind === "rework_pause") return "Rework paused";
  if (approval.kind === "cli_permission") return "Agent permission";
  if (approval.kind === "stage_park") return "Environment override";
  return "Agent question";
}

/**
 * The human side of a ticket: what has to be true before sign-off, and the
 * approvals currently asking for it — at full width, so a long criteria list or
 * testing checklist is readable instead of clamped into the inbox rail.
 */
export function ApprovalsView({ ticket }: { ticket?: TicketDetail }) {
  const qc = useQueryClient();
  const ticketId = ticket?.id;

  const approvals = useQuery({
    queryKey: ["approvals", ticketId],
    queryFn: () => api.approvals(ticketId),
    refetchInterval: 5000,
    enabled: !!ticketId,
  });

  const resolveApproval = useMutation({
    meta: { errorTitle: "Resolve approval" },
    mutationFn: ({
      id,
      action,
      payload,
    }: {
      id: string;
      action: "approve" | "recheck" | "reject";
      payload?: ApprovalResolvePayload;
    }) => api.resolveApproval(id, { action, ...payload }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["approval-history"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  if (!ticket) {
    return <div className="approvals-view-empty">No ticket selected</div>;
  }

  const pending = approvals.data ?? [];
  const criteria = ticket.acceptance_criteria ?? [];
  const humanPending = pending.filter(hasHumanCriteria);
  const otherPending = pending.filter((a) => !hasHumanCriteria(a));
  // A gate whose checklist walks the criteria one by one already shows them, in
  // the form you can work through — listing them again above adds nothing.
  const checklistShowsCriteria = humanPending.some((approval) =>
    checklistCoversCriteria(approval.checklist ?? [], criteria),
  );

  const renderRow = (approval: Approval) => (
    <ApprovalRow
      key={approval.id}
      approval={approval}
      ticketId={ticket.id}
      criteria={criteria}
      isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === approval.id}
      onResolve={(action, payload) => resolveApproval.mutate({ id: approval.id, action, payload })}
    />
  );

  return (
    <div className="approvals-view">
      {resolveApproval.isError && (
        <div className="approvals-view-error" role="alert">
          {formatApprovalResolveError(resolveApproval.error)}
        </div>
      )}

      {approvals.isError ? (
        // Never the empty state: "nothing needs you" is a claim this view
        // cannot make when it failed to ask.
        <div className="approvals-view-error" role="alert">
          Could not load this ticket&rsquo;s approvals: {describeError(approvals.error, "the request failed")}.
          Retrying every few seconds.
        </div>
      ) : approvals.isLoading ? (
        <div className="approvals-view-note">Loading approvals…</div>
      ) : pending.length === 0 ? (
        <NothingWaiting ticket={ticket} />
      ) : null}

      {humanPending.length > 0 && (
        <section aria-label="Awaiting your sign-off">
          <div className="state-label">Awaiting your sign-off ({humanPending.length})</div>
          {humanPending.map(renderRow)}
        </section>
      )}

      {otherPending.length > 0 && (
        <section aria-label="Other pending approvals">
          <div className="state-label">Other pending approvals ({otherPending.length})</div>
          {otherPending.map(renderRow)}
        </section>
      )}

      <section aria-label="Past decisions">
        <div className="state-label">Past decisions</div>
        <ApprovalHistoryList ticketId={ticket.id} />
      </section>

      {/* Reference, not a task: on real tickets it runs to twenty items and
          several KB, so it stays folded behind its count until asked for. */}
      {!checklistShowsCriteria && (
        <details className="approvals-view-criteria-block">
          <summary className="approvals-view-criteria-summary">
            <span className="state-label">Acceptance criteria</span>
            <span className="approvals-view-count">{criteria.length}</span>
          </summary>
          {criteria.length ? (
            <ol className="approvals-view-criteria">
              {criteria.map((item, idx) => (
                <li key={idx}>
                  <MarkdownContent content={item} className="approvals-view-criterion" expandable={false} />
                </li>
              ))}
            </ol>
          ) : (
            <div className="approvals-view-note">No acceptance criteria recorded on this ticket.</div>
          )}
        </details>
      )}
    </div>
  );
}

/** Where the ticket is, in the words an operator would use for it. */
function whereItIs(ticket: TicketDetail): string {
  if (ticket.state === "done") return "This ticket is done.";
  if (ticket.state === "backlog") return "This ticket has not started.";
  const stage =
    ticket.stages.find((s) => s.status === "running") ??
    ticket.stages.find((s) => s.status === "blocked") ??
    ticket.stages.find((s) => s.status === "awaiting");
  if (!stage) return "";
  if (stage.status === "running") return `${stage.name} is running.`;
  if (stage.status === "blocked") return `${stage.name} is blocked.`;
  return `${stage.name} is waiting to continue.`;
}

/**
 * The empty state — the common one: 12 approvals are pending across 1,276
 * tickets. It says so plainly, says where the work is, and points at the one
 * place that explains a stall.
 */
function NothingWaiting({ ticket }: { ticket: TicketDetail }) {
  const where = whereItIs(ticket);
  return (
    <div className="approvals-view-status" role="status">
      <div className="approvals-view-status-title">Nothing needs you right now</div>
      <div className="approvals-view-status-body">
        {where ? `${where} ` : ""}Sign-offs and agent questions appear here, and in your Inbox, when a stage asks.
      </div>
      <Button
        variant="plain"
        className="approval-inline-toggle"
        onClick={() => navigateToTicketTab(ticket.id, "timeline")}
      >
        Open the Timeline
      </Button>
    </div>
  );
}

/**
 * The scope hint matters here: the approvals list covers the ticket's whole
 * subtree, so a card can belong to a child ticket rather than the open one.
 */
function ApprovalRow({
  approval,
  ticketId,
  criteria,
  isSubmitting,
  onResolve,
}: {
  approval: Approval;
  ticketId: string;
  /** Listed above the cards — what the brief and the checklist need not restate. */
  criteria: string[];
  isSubmitting: boolean;
  onResolve: (action: "approve" | "recheck" | "reject", payload?: ApprovalResolvePayload) => void;
}) {
  const deduped = criteria.length > 0;
  return (
    <div>
      {approval.ticket_id && approval.ticket_id !== ticketId && approval.ticket_external_id && (
        <div className="approvals-view-scope-hint">
          {kindLabel(approval)} · {approval.ticket_external_id}
        </div>
      )}
      <ApprovalCard
        approval={approval}
        impactText={deduped ? impactWithoutCriteria(approval.impact) : undefined}
        clampBrief
        isSubmitting={isSubmitting}
        onApprove={(payload) => onResolve("approve", payload)}
        onRecheck={(payload) => onResolve("recheck", payload)}
        onReject={(payload) => onResolve("reject", payload)}
      />
    </div>
  );
}

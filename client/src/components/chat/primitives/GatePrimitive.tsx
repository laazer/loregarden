import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Approval } from "../../../api/client";
import type { TicketDetail, WorkflowStageView } from "../../../api/types";
import { ApprovalCard, type ApprovalResolvePayload } from "../../ApprovalCard";
import { CIApprovalGateCheck } from "../../CIApprovalGateCheck";
import { useApprovalResolution } from "../../../hooks/useApprovalResolution";
import type { GatePart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenGateStudioButton, OpenTicketButton } from "./ResourceActionButton";
import { ticketQueryRetry, ticketRefetchInterval } from "./ticketLiveQuery";

function draftChecks(draft: Record<string, unknown>): string[] {
  const checklist = draft.checklist;
  if (Array.isArray(checklist)) {
    return checklist.filter((item): item is string => typeof item === "string" && Boolean(item.trim()));
  }
  const checks = draft.gate_checks;
  if (!Array.isArray(checks)) return [];
  return checks.flatMap((check) => {
    if (!check || typeof check !== "object") return [];
    const title = (check as { title?: unknown }).title;
    return typeof title === "string" && title.trim() ? [title] : [];
  });
}

function gateTone(status: string | undefined): "default" | "accent" | "warn" | "danger" | "ok" {
  if (status === "blocked") return "danger";
  if (status === "done") return "ok";
  if (status === "awaiting" || status === "running") return "warn";
  return "default";
}

function statusCopy(status: string | undefined): string {
  switch (status) {
    case "awaiting":
      return "Waiting on operator sign-off";
    case "blocked":
      return "Blocked — needs attention before the workflow can continue";
    case "running":
      return "Gate evaluation in progress";
    case "done":
      return "Gate passed";
    case "pending":
      return "Not reached yet";
    default:
      return "Gate preview";
  }
}

function CriteriaList({ items, title }: { items: string[]; title: string }) {
  if (!items.length) return null;
  return (
    <div className="lg-primitive-gate-section">
      <p className="lg-primitive-gate-section-title">{title}</p>
      <ul className="lg-primitive-gate-checklist">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function GateApprovals({
  ticketId,
  approvals,
}: {
  ticketId: string;
  approvals: Approval[];
}) {
  const resolve = useApprovalResolution(ticketId);
  if (!approvals.length) return null;

  return (
    <div className="lg-primitive-gate-section">
      <p className="lg-primitive-gate-section-title">
        Pending sign-off · {approvals.length}
      </p>
      {approvals.map((approval) => (
        <ApprovalCard
          key={approval.id}
          approval={approval}
          compact
          isSubmitting={resolve.isPending && resolve.variables?.id === approval.id}
          onApprove={(payload?: ApprovalResolvePayload) =>
            resolve.mutate({ id: approval.id, action: "approve", ...payload })
          }
          onReject={(payload?: ApprovalResolvePayload) =>
            resolve.mutate({ id: approval.id, action: "reject", ...payload })
          }
        />
      ))}
      {resolve.isError ? (
        <p className="lg-primitive-card-error" role="alert">
          {resolve.error instanceof Error ? resolve.error.message : "Failed to resolve approval"}
        </p>
      ) : null}
    </div>
  );
}

function LiveGateBody({
  ticket,
  stage,
  approvals,
}: {
  ticket: TicketDetail;
  stage: WorkflowStageView | undefined;
  approvals: Approval[];
}) {
  const qc = useQueryClient();
  const advance = useMutation({
    meta: { errorTitle: "Advance ticket" },
    mutationFn: () => api.advance(ticket.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
      void qc.invalidateQueries({ queryKey: ["approvals", ticket.id] });
    },
  });

  const canAdvance =
    stage?.status === "awaiting" &&
    approvals.length === 0 &&
    !advance.isPending;

  return (
    <div className="lg-primitive-gate">
      <div className="lg-primitive-gate-status">
        <p className="lg-primitive-gate-status-label">
          {stage?.name ?? "Gate"} · {(stage?.status ?? "pending").replace("_", " ")}
        </p>
        <p className="lg-primitive-gate-status-copy">{statusCopy(stage?.status)}</p>
        {ticket.external_id ? (
          <p className="lg-primitive-gate-status-meta">
            {ticket.external_id}
            {stage?.agent_id ? ` · ${stage.agent_id}` : ""}
          </p>
        ) : null}
      </div>

      <CIApprovalGateCheck ticketId={ticket.id} />

      <CriteriaList
        items={ticket.acceptance_criteria ?? []}
        title="Acceptance criteria"
      />

      <GateApprovals ticketId={ticket.id} approvals={approvals} />

      {canAdvance ? (
        <div className="lg-primitive-gate-section">
          <p className="lg-primitive-gate-section-title">Ready to continue</p>
          <p className="lg-primitive-card-sub">
            No pending gate approvals. Advance the ticket past this gate when the evidence looks good.
          </p>
          <button
            type="button"
            className="lg-primitive-run-btn lg-primitive-run-btn--confirm"
            disabled={advance.isPending}
            onClick={() => void advance.mutate()}
          >
            {advance.isPending ? "Advancing…" : "Advance past gate"}
          </button>
          {advance.isError ? (
            <p className="lg-primitive-card-error" role="alert">
              {advance.error instanceof Error ? advance.error.message : "Advance failed"}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function DraftGateBody({ draft }: { draft: Record<string, unknown> }) {
  const description =
    typeof draft.description === "string"
      ? draft.description
      : typeof draft.impact === "string"
        ? draft.impact
        : null;
  const checks = draftChecks(draft);

  return (
    <div className="lg-primitive-gate">
      <div className="lg-primitive-gate-status lg-primitive-gate-status--draft">
        <p className="lg-primitive-gate-status-label">Draft gate preview</p>
        <p className="lg-primitive-gate-status-copy">
          This card previews a gate definition. Save it in Gate Studio to attach it to a workflow.
        </p>
      </div>
      {description ? <p className="lg-primitive-card-sub">{description}</p> : null}
      <CriteriaList items={checks} title="Gate checks" />
      {!description && !checks.length ? (
        <p className="lg-primitive-card-sub">
          Add a description or checklist to the draft so operators know what this gate enforces.
        </p>
      ) : null}
    </div>
  );
}

export function GatePrimitive({ part }: { part: GatePart }) {
  const ticketId = part.ticket_id ?? undefined;
  const draft = part.draft ?? {};

  const ticketQuery = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId!),
    enabled: Boolean(ticketId),
    retry: ticketQueryRetry,
    refetchInterval: ticketRefetchInterval,
  });

  const approvalsQuery = useQuery({
    queryKey: ["approvals", ticketId],
    queryFn: () => api.approvals(ticketId),
    enabled: Boolean(ticketId) && !ticketQuery.isError,
    refetchInterval: ticketQuery.isError ? false : 4000,
  });

  const ticket = ticketQuery.data;
  const stage =
    ticket?.stages?.find((s) => s.key === part.stage_key) ??
    ticket?.stages?.find((s) => s.stage_type === "gate");
  const pendingGates = (approvalsQuery.data ?? []).filter(
    (approval) =>
      (!approval.status || approval.status === "pending") &&
      approval.kind === "workflow_gate" &&
      (!part.stage_key || approval.stage_key === part.stage_key || !approval.stage_key),
  );

  const title =
    part.title ??
    stage?.name ??
    (typeof draft.name === "string" ? draft.name : null) ??
    (typeof draft.title === "string" ? draft.title : null) ??
    "Gate";

  return (
    <PrimitiveCard
      title={title}
      subtitle={
        ticket
          ? `${ticket.external_id || ticket.id.slice(0, 8)} · ${part.stage_key ?? stage?.key ?? "gate"}`
          : (part.stage_key ?? stage?.key ?? undefined)
      }
      loading={Boolean(ticketId) && ticketQuery.isLoading}
      error={
        ticketId && ticketQuery.error
          ? ticketQuery.error instanceof Error
            ? ticketQuery.error.message
            : "Failed to load gate"
          : null
      }
      tone={gateTone(stage?.status ?? (pendingGates.length ? "awaiting" : undefined))}
      meta={
        <>
          {stage ? <span>{stage.status.replace("_", " ")}</span> : null}
          {pendingGates.length ? <span>{pendingGates.length} pending</span> : null}
          {stage?.agent_id ? <span>{stage.agent_id}</span> : null}
          {ticket?.workspace_slug ? <span>{ticket.workspace_slug}</span> : null}
        </>
      }
      resourceAction={
        ticketId ? (
          <>
            <OpenTicketButton ticketId={ticketId} />
          </>
        ) : (
          <OpenGateStudioButton />
        )
      }
    >
      {ticket ? (
        <LiveGateBody ticket={ticket} stage={stage} approvals={pendingGates} />
      ) : ticketId && ticketQuery.isLoading ? null : part.draft ? (
        <DraftGateBody draft={draft} />
      ) : ticketId ? null : (
        <p className="lg-primitive-card-sub">
          Provide a ticket_id for a live gate, or a draft to preview a gate definition.
        </p>
      )}
    </PrimitiveCard>
  );
}

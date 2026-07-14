import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Approval, type RuntimeOptions, type TicketDetail } from "../api/client";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import { StudioChatMessages } from "./studio/StudioChat";
import { TreeExpandChevron } from "./icons/TicketTreeIcons";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { TriageComposer } from "./TriageComposer";

function mergeApprovals(...lists: Array<Approval[] | undefined>): Approval[] {
  const seen = new Set<string>();
  const merged: Approval[] = [];
  for (const list of lists) {
    for (const item of list ?? []) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      merged.push(item);
    }
  }
  return merged;
}

function approvalKindLabel(kind: Approval["kind"]) {
  switch (kind) {
    case "workflow_gate":
      return "Stage sign-off";
    case "cli_permission":
      return "Agent permission";
    case "cli_question":
      return "Agent question";
    default:
      return kind;
  }
}

function ResolvedApprovalSummary({ approval }: { approval: Approval }) {
  const answers = approval.resolved_answers;
  return (
    <div
      style={{
        padding: 10,
        borderRadius: 10,
        border: "1px solid var(--bd)",
        background: "var(--bg3)",
        marginBottom: 8,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600 }}>{approval.title}</div>
      <div style={{ fontSize: 10.5, color: "var(--txl)", marginTop: 4 }}>
        {approvalKindLabel(approval.kind)} · {approval.stage_name} · {approval.status}
      </div>
      {answers && (
        <pre
          style={{
            marginTop: 8,
            fontFamily: "var(--mono)",
            fontSize: 10.5,
            whiteSpace: "pre-wrap",
            color: "var(--txm)",
          }}
        >
          {JSON.stringify(answers, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function TriagePanel({
  ticket,
  runtimeOptions,
  onResolved,
}: {
  ticket: TicketDetail | undefined;
  runtimeOptions: RuntimeOptions | undefined;
  onResolved?: () => void;
}) {
  const qc = useQueryClient();
  const [recentExpanded, setRecentExpanded] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const triage = useQuery({
    queryKey: ["triage", ticket?.id],
    queryFn: () => api.triage(ticket!.id),
    enabled: !!ticket?.id,
    retry: 1,
    refetchInterval: (query) => {
      const pending = query.state.data?.pending_approvals?.length ?? 0;
      const busy = query.state.data ? query.state.data.run_status !== "idle" : false;
      return pending > 0 || busy ? 2000 : 5000;
    },
  });

  const isSending = triage.data ? triage.data.run_status !== "idle" : false;

  const ticketApprovals = useQuery({
    queryKey: ["approvals", ticket?.id],
    queryFn: () => api.approvals(ticket!.id),
    enabled: !!ticket?.id,
    refetchInterval: 2000,
  });

  const resolveApproval = useMutation({
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
      qc.invalidateQueries({ queryKey: ["triage", ticket?.id] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket", ticket?.id] });
      qc.invalidateQueries({ queryKey: ["runs", ticket?.id] });
      onResolved?.();
    },
  });

  const messages = triage.data?.messages ?? [];
  const pending = mergeApprovals(triage.data?.pending_approvals, ticketApprovals.data);
  const recent = triage.data?.recent_approvals ?? [];

  useEffect(() => {
    setRecentExpanded(false);
    setAutoScroll(true);
  }, [ticket?.id]);

  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>Select a ticket to triage</div>;
  }

  return (
    <div className="triage-panel-shell">
      <div className="triage-panel-body">
        {triage.isError && (
          <div className="triage-panel-alert">
            Triage API unavailable — showing inbox approvals for this ticket.
            Restart the dev server if chat is missing.
          </div>
        )}

        {recent.length > 0 && (
          <section className="triage-panel-section">
            <button
              type="button"
              onClick={() => setRecentExpanded((open) => !open)}
              aria-expanded={recentExpanded}
              className="triage-panel-section-toggle"
            >
              <TreeExpandChevron expanded={recentExpanded} />
              <span className="state-label" style={{ marginBottom: 0 }}>
                Recently resolved
              </span>
              <span className="triage-panel-section-count">{recent.length}</span>
            </button>
            {recentExpanded && (
              <div className="triage-panel-section-scroll">
                {recent.map((approval) => (
                  <ResolvedApprovalSummary key={approval.id} approval={approval} />
                ))}
              </div>
            )}
          </section>
        )}

        <section className="triage-chat-section">
          <div className="ticket-studio-chat-header">
            <div>
              <div className="ticket-studio-chat-title">Triage chat</div>
              <div className="ticket-studio-chat-sub">
                Ask about requirements, failures, or next steps for this ticket.
              </div>
            </div>
          </div>
          <StudioChatMessages
            messages={messages}
            assistantLabel={TRIAGE_AGENT_NAME}
            thinkingActivity="typing"
            autoScroll={autoScroll}
            emptyMessage={`Ask about requirements, failures, or next steps. ${TRIAGE_AGENT_NAME} sees this ticket's description, workflow state, blocking issues, recent runs, and this conversation.`}
            isThinking={isSending}
            thinkingMessage={`${TRIAGE_AGENT_NAME} is thinking…`}
          />
        </section>
      </div>

      <PendingApprovalsSection
        approvals={pending}
        ticketExternalId={ticket.external_id}
        submittingApprovalId={resolveApproval.isPending ? resolveApproval.variables?.id ?? null : null}
        submitError={resolveApproval.isError ? formatApprovalResolveError(resolveApproval.error) : null}
        onApprove={(approval, payload) =>
          resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })
        }
        onReject={(approval, payload) =>
          resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })
        }
      />

      <TriageComposer
        ticketId={ticket.id}
        runtimeOptions={runtimeOptions}
        showAutoScrollToggle
        autoScroll={autoScroll}
        onAutoScrollChange={setAutoScroll}
        onSent={onResolved}
      />
    </div>
  );
}

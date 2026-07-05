import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type Approval, type RuntimeOptions, type TicketDetail, type TriageMessage } from "../api/client";
import { formatApprovalResolveError } from "../utils/approvalErrors";
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

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
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
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const [recentExpanded, setRecentExpanded] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const triage = useQuery({
    queryKey: ["triage", ticket?.id],
    queryFn: () => api.triage(ticket!.id),
    enabled: !!ticket?.id,
    retry: 1,
    refetchInterval: (query) => {
      const pending = query.state.data?.pending_approvals?.length ?? 0;
      return pending > 0 ? 2000 : 5000;
    },
  });

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
    }: {
      id: string;
      action: "approve" | "reject";
      answers?: Record<string, string | string[]>;
      response?: string;
    }) => api.resolveApproval(id, { action, answers, response }),
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
  const triageUnavailable = triage.isError && pending.length === 0;

  useEffect(() => {
    setRecentExpanded(false);
    setAutoScroll(true);
  }, [ticket?.id]);

  useEffect(() => {
    if (!autoScroll) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [autoScroll, messages.length, isSending]);

  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>Select a ticket to triage</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 340 }}>
      <div style={{ flex: 1, overflow: "auto", padding: 16, minHeight: 0 }}>
        {triage.isError && (
          <div
            style={{
              fontSize: 11.5,
              color: "var(--rdl)",
              marginBottom: 16,
              padding: "8px 10px",
              borderRadius: 8,
              background: "rgba(240,96,63,.08)",
              border: "1px solid rgba(240,96,63,.25)",
            }}
          >
            Triage API unavailable — showing inbox approvals for this ticket.
            Restart the dev server if chat is missing.
          </div>
        )}

        {recent.length > 0 && (
          <section style={{ marginBottom: 20 }}>
            <button
              type="button"
              onClick={() => setRecentExpanded((open) => !open)}
              aria-expanded={recentExpanded}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                width: "100%",
                padding: 0,
                marginBottom: recentExpanded ? 10 : 0,
                border: "none",
                background: "transparent",
                cursor: "pointer",
                textAlign: "left",
              }}
            >
              <span className={`tree-chevron-icon ${recentExpanded ? "expanded" : ""}`} />
              <span className="state-label" style={{ marginBottom: 0 }}>
                Recently resolved
              </span>
              <span
                style={{
                  fontSize: 10,
                  color: "var(--txl)",
                  fontWeight: 600,
                  letterSpacing: "0.04em",
                }}
              >
                {recent.length}
              </span>
            </button>
            {recentExpanded && (
              <div
                style={{
                  maxHeight: 220,
                  overflowY: "auto",
                  paddingRight: 4,
                }}
              >
                {recent.map((approval) => (
                  <ResolvedApprovalSummary key={approval.id} approval={approval} />
                ))}
              </div>
            )}
          </section>
        )}

        <section>
          <div className="state-label" style={{ marginBottom: 10 }}>
            Triage chat
          </div>
          <div
            style={{
              border: "1px solid var(--bd)",
              borderRadius: 12,
              background: "var(--bg2)",
              padding: 12,
              minHeight: 220,
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            {triageUnavailable ? (
              <div style={{ fontSize: 12, color: "var(--txm)" }}>
                Could not load triage data. Restart <code>./scripts/dev-server.sh</code> and refresh.
              </div>
            ) : messages.length === 0 && !isSending ? (
              <div style={{ fontSize: 12, color: "var(--txm)", lineHeight: 1.55 }}>
                Ask about requirements, failures, or next steps. The triage assistant sees this ticket&apos;s
                description, workflow state, blocking issues, recent runs, and this conversation.
              </div>
            ) : (
              messages.map((msg: TriageMessage) => (
                <div
                  key={msg.id}
                  style={{
                    alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
                    maxWidth: "92%",
                    padding: "10px 12px",
                    borderRadius: 10,
                    background: msg.role === "user" ? "rgba(96,165,250,.12)" : "var(--bg3)",
                    border: `1px solid ${msg.role === "user" ? "rgba(96,165,250,.25)" : "var(--bd)"}`,
                  }}
                >
                  <div style={{ fontSize: 10, color: "var(--txl)", marginBottom: 4 }}>
                    {msg.role === "user" ? "You" : "Triage assistant"} · {formatTime(msg.created_at)}
                  </div>
                  <div style={{ fontSize: 12.5, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{msg.content}</div>
                </div>
              ))
            )}
            {isSending && (
              <div style={{ fontSize: 12, color: "var(--bll)" }}>Triage assistant is thinking…</div>
            )}
            <div ref={bottomRef} />
          </div>
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
        onReject={(approval) => resolveApproval.mutate({ id: approval.id, action: "reject" })}
      />

      <TriageComposer
        ticketId={ticket.id}
        runtimeOptions={runtimeOptions}
        showAutoScrollToggle
        autoScroll={autoScroll}
        onAutoScrollChange={setAutoScroll}
        onSendStart={() => setIsSending(true)}
        onSendEnd={() => setIsSending(false)}
        onSent={onResolved}
      />
    </div>
  );
}

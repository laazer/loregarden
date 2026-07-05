import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type Approval, type RuntimeOptions, type TicketDetail, type TriageMessage } from "../api/client";
import { formatLogExcerpt } from "../utils/logExcerpt";
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

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function LogsPanel({
  ticket,
  runtimeOptions,
  onResolved,
}: {
  ticket: TicketDetail;
  runtimeOptions: RuntimeOptions | undefined;
  onResolved?: () => void;
}) {
  const qc = useQueryClient();
  const logScrollRef = useRef<HTMLDivElement | null>(null);
  const [showTriageReplies, setShowTriageReplies] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const triage = useQuery({
    queryKey: ["triage", ticket.id],
    queryFn: () => api.triage(ticket.id),
    enabled: !!ticket.id,
    retry: 1,
    refetchInterval: (query) => {
      const pending = query.state.data?.pending_approvals?.length ?? 0;
      return pending > 0 ? 2000 : 5000;
    },
  });

  const ticketApprovals = useQuery({
    queryKey: ["approvals", ticket.id],
    queryFn: () => api.approvals(ticket.id),
    enabled: !!ticket.id,
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
      qc.invalidateQueries({ queryKey: ["triage", ticket.id] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
      qc.invalidateQueries({ queryKey: ["runs", ticket.id] });
      onResolved?.();
    },
  });

  const lines = ticket.artifacts?.logs ?? [];
  const live = ticket.artifacts?.live ?? null;
  const pending = mergeApprovals(triage.data?.pending_approvals, ticketApprovals.data);
  const messages = triage.data?.messages ?? [];
  const recentReplies = useMemo(
    () => messages.filter((msg: TriageMessage) => msg.role === "assistant").slice(-2),
    [messages],
  );

  useEffect(() => {
    if (!autoScroll) return;
    const node = logScrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [autoScroll, lines.length, live, ticket.id]);

  const logContext = () => formatLogExcerpt(lines, live);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 340 }}>
      <div ref={logScrollRef} style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, padding: 16, lineHeight: 1.75 }}>
          {lines.length === 0 && !live ? (
            <div style={{ color: "var(--txm)", fontFamily: "inherit", fontSize: 12 }}>
              No log lines yet. Start a stage run or ask triage about failures below.
            </div>
          ) : (
            <>
              {lines.map((l, i) => (
                <div key={i} style={{ display: "flex", gap: 12 }}>
                  <span style={{ color: "var(--txl)" }}>{l.time}</span>
                  <span style={{ width: 44, textAlign: "center", fontSize: 10, fontWeight: 600 }}>
                    {l.tag}
                  </span>
                  <span style={{ color: "var(--txm)", whiteSpace: "pre-wrap" }}>{l.text}</span>
                </div>
              ))}
              {live && (
                <div style={{ color: "var(--bll)", marginTop: 8, whiteSpace: "pre-wrap" }}>
                  {live} ▊
                </div>
              )}
            </>
          )}
        </div>

        {recentReplies.length > 0 && (
          <section style={{ padding: "0 16px 16px" }}>
            <button
              type="button"
              onClick={() => setShowTriageReplies((open) => !open)}
              style={{
                border: "none",
                background: "transparent",
                color: "var(--txl)",
                fontSize: 11,
                cursor: "pointer",
                padding: 0,
                marginBottom: 8,
              }}
            >
              {showTriageReplies ? "Hide" : "Show"} triage replies ({recentReplies.length})
            </button>
            {showTriageReplies &&
              recentReplies.map((msg) => (
                <div
                  key={msg.id}
                  style={{
                    padding: "10px 12px",
                    borderRadius: 10,
                    background: "var(--bg3)",
                    border: "1px solid var(--bd)",
                    marginBottom: 8,
                  }}
                >
                  <div style={{ fontSize: 10, color: "var(--txl)", marginBottom: 4 }}>
                    Triage assistant · {formatTime(msg.created_at)}
                  </div>
                  <div style={{ fontSize: 12.5, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                    {msg.content}
                  </div>
                </div>
              ))}
          </section>
        )}
      </div>

      <PendingApprovalsSection
        approvals={pending}
        ticketExternalId={ticket.external_id}
        isSubmitting={resolveApproval.isPending}
        onApprove={(approval, payload) =>
          resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })
        }
        onReject={(approval) => resolveApproval.mutate({ id: approval.id, action: "reject" })}
      />

      <TriageComposer
        ticketId={ticket.id}
        runtimeOptions={runtimeOptions}
        placeholder="Ask triage about these logs, failures, or next steps…"
        attachLogContext={logContext}
        showAttachLogsToggle
        attachLogsDefault
        showAutoScrollToggle
        autoScroll={autoScroll}
        onAutoScrollChange={setAutoScroll}
        onSent={onResolved}
      />
    </div>
  );
}

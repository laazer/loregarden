import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type Approval, type LogLine, type RuntimeOptions, type TicketDetail, type TriageMessage } from "../api/client";
import { logTagVariant } from "../lib/logLineStyle";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { formatLogExcerpt } from "../utils/logExcerpt";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import { ChatMessageBubble } from "./chat/ChatMessageBubble";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { TriageComposer } from "./TriageComposer";
import "./LogsPanel.css";

function LogLineRow({ line }: { line: LogLine }) {
  const variant = logTagVariant(line.tag);
  return (
    <div className="log-line">
      <span className="log-line__time">{line.time}</span>
      <span className={`log-line__tag log-line__tag--${variant}`}>{line.tag}</span>
      <span className="log-line__text">{line.text}</span>
    </div>
  );
}

function LiveLogLine({ text }: { text: string }) {
  return (
    <div className="log-line log-line--live">
      <span className="log-line__time">now</span>
      <span className="log-line__tag log-line__tag--run log-line__tag--live">RUN</span>
      <span className="log-line__text">
        {text}
        <span className="log-line__cursor" aria-hidden>
          ▊
        </span>
      </span>
    </div>
  );
}

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
      always_allow,
      allow_for_ticket,
      allow_for_stage,
    }: {
      id: string;
      action: "approve" | "reject";
      answers?: Record<string, string | string[]>;
      response?: string;
      always_allow?: boolean;
      allow_for_ticket?: boolean;
      allow_for_stage?: boolean;
    }) => api.resolveApproval(id, { action, answers, response, always_allow, allow_for_ticket, allow_for_stage }),
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
        <div className="log-feed">
          {lines.length === 0 && !live ? (
            <div className="log-feed-empty">
              No log lines yet. Start a stage run or ask {TRIAGE_AGENT_NAME} about failures below.
            </div>
          ) : (
            <>
              {lines.map((line, index) => (
                <LogLineRow key={`${line.time}-${line.tag}-${index}`} line={line} />
              ))}
              {live ? <LiveLogLine text={live} /> : null}
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
                <ChatMessageBubble key={msg.id} message={msg} assistantLabel={TRIAGE_AGENT_NAME} />
              ))}
          </section>
        )}
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
        placeholder={`Ask ${TRIAGE_AGENT_NAME} about these logs, failures, or next steps…`}
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

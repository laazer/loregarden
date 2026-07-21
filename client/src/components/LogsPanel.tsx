import { useEffect, useMemo, useRef, useState } from "react";

import type { RuntimeOptions, TicketDetail, TriageMessage } from "../api/client";

import { formatApprovalResolveError } from "../utils/approvalErrors";
import { formatLogExcerpt } from "../utils/logExcerpt";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import { ChatMessageBubble } from "./chat/ChatMessageBubble";
import { LiveLogLine, LogLineRow } from "./logs/LogLineRow";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { TriageComposer } from "./TriageComposer";
import "./LogsPanel.css";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { useTriageSession } from "../hooks/useTriageSession";

export function LogsPanel({
  ticket,
  runtimeOptions,
  onResolved,
}: {
  ticket: TicketDetail;
  runtimeOptions: RuntimeOptions | undefined;
  onResolved?: () => void;
}) {
  const logScrollRef = useRef<HTMLDivElement | null>(null);
  const [showTriageReplies, setShowTriageReplies] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const { triage, pending } = useTriageSession(ticket.id);

  const resolveApproval = useApprovalResolution(ticket.id);

  const lines = ticket.artifacts?.logs ?? [];
  const live = ticket.artifacts?.live ?? null;
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
        onReject={(approval, payload) =>
          resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })
        }
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

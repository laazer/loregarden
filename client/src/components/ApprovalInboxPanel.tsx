import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Approval } from "../api/client";
import { navigateToTicket } from "../lib/useAppNavigation";
import { useNotificationStore } from "../state/notificationStore";
import { useUiStore } from "../state/uiStore";
import { hasHumanCriteria } from "../utils/approvalCriteria";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { IconCloseButton } from "./IconCloseButton";
import { ApprovalCard, type ApprovalResolvePayload } from "./ApprovalCard";
import { ApprovalDetailModal } from "./ApprovalDetailModal";

function toneAccent(tone: string): string {
  if (tone === "error") return "var(--rdl)";
  if (tone === "success") return "var(--grn)";
  if (tone === "warning") return "var(--aml)";
  return "var(--txm)";
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Combined approvals + queue notifications drawer.
 *
 * Approvals need action; notifications are a durable log of run events that
 * survive toast dismiss and clear individually or all at once.
 */
export function ApprovalInboxPanel() {
  const qc = useQueryClient();
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const [expandedApproval, setExpandedApproval] = useState<Approval | null>(null);
  const notifications = useNotificationStore((s) => s.notifications);
  const dismissNotification = useNotificationStore((s) => s.dismiss);
  const clearNotifications = useNotificationStore((s) => s.clear);

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
    enabled: inboxOpen,
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

  /** Inspect lands on the criteria for a human gate, on the diff otherwise. */
  const inspectApproval = (approval: Approval) => {
    if (!approval.ticket_id) return;
    navigateToTicket(approval.ticket_id, {
      tab: hasHumanCriteria(approval) ? "approvals" : "diff",
    });
    setExpandedApproval(null);
    setInboxOpen(false);
  };

  if (!inboxOpen) return null;

  const approvalCount = approvals.data?.length ?? 0;
  const notificationCount = notifications.length;
  const totalCount = approvalCount + notificationCount;
  const empty = approvalCount === 0 && notificationCount === 0;

  return (
    <>
      <div className="inbox-overlay" onClick={() => setInboxOpen(false)} />
      <aside className="inbox-panel" aria-label="Approvals and notifications">
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--bd)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="pane-title">Inbox</span>
            <span className="count-pill">{totalCount}</span>
            <div style={{ flex: 1 }} />
            <IconCloseButton onClick={() => setInboxOpen(false)} />
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
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

          {!empty ? (
            <>
          <section className="inbox-section" aria-label="Pending approvals">
            <div className="inbox-section-header">
              <span className="inbox-section-title">Approvals</span>
              <span className="count-pill">{approvalCount}</span>
            </div>
            {approvals.data?.map((a) => (
              <ApprovalCard
                key={a.id}
                approval={a}
                onApprove={(payload) =>
                  resolveApproval.mutate({ id: a.id, action: "approve", ...payload })
                }
                onReject={(payload) =>
                  resolveApproval.mutate({ id: a.id, action: "reject", ...payload })
                }
                onInspect={a.ticket_id ? () => inspectApproval(a) : undefined}
                inspectLabel={hasHumanCriteria(a) ? "Approvals tab" : "Inspect"}
                collapsible
                onExpand={() => setExpandedApproval(a)}
                isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === a.id}
              />
            ))}
            {!approvalCount ? (
              <div className="inbox-empty-hint">No pending approvals</div>
            ) : null}
          </section>

          <section className="inbox-section" aria-label="Run notifications">
            <div className="inbox-section-header">
              <span className="inbox-section-title">Notifications</span>
              <span className="count-pill">{notificationCount}</span>
              <div style={{ flex: 1 }} />
              {notificationCount > 0 ? (
                <button
                  type="button"
                  className="btn-secondary inbox-clear-btn"
                  onClick={() => clearNotifications()}
                >
                  Clear all
                </button>
              ) : null}
            </div>
            {notifications.map((n) => (
              <article
                key={n.id}
                className="inbox-notification-card"
                style={{ borderLeftColor: toneAccent(n.tone) }}
              >
                <div className="inbox-notification-top">
                  <div>
                    <div className="inbox-notification-title">{n.title}</div>
                    {n.message ? (
                      <div className="inbox-notification-message">{n.message}</div>
                    ) : null}
                    <div className="inbox-notification-meta">{formatWhen(n.createdAt)}</div>
                  </div>
                  <button
                    type="button"
                    className="btn-secondary inbox-clear-btn"
                    aria-label="Dismiss notification"
                    onClick={() => dismissNotification(n.id)}
                  >
                    Clear
                  </button>
                </div>
                {n.ticketId ? (
                  <button
                    type="button"
                    className="btn-secondary inbox-notification-link"
                    onClick={() => {
                      navigateToTicket(n.ticketId!, { tab: "diff" });
                      setInboxOpen(false);
                    }}
                  >
                    Open ticket
                  </button>
                ) : null}
              </article>
            ))}
            {!notificationCount ? (
              <div className="inbox-empty-hint">No notifications yet</div>
            ) : null}
          </section>
            </>
          ) : (
            <div style={{ textAlign: "center", color: "var(--txm)", padding: 40 }}>
              Inbox zero — nothing needs your attention
            </div>
          )}
        </div>
      </aside>
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
          expandedApproval?.ticket_id ? () => inspectApproval(expandedApproval) : undefined
        }
      />
    </>
  );
}

import { useDialogDismiss } from "../hooks/useDialogDismiss";
import { type Approval } from "../api/client";
import { navigateToTicket } from "../lib/useAppNavigation";
import { useNotificationStore } from "../state/notificationStore";
import { useUiStore } from "../state/uiStore";
import { hasHumanCriteria } from "../utils/approvalCriteria";
import { IconCloseButton } from "./IconCloseButton";
import { ApprovalsList, usePendingApprovalCount } from "./inbox/ApprovalsList";

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
 *
 * The approvals half now lives in `inbox/ApprovalsList`, because a view pane
 * wants exactly that half and none of what makes this a drawer. What stays here
 * is what is genuinely singular: the open flag, the overlay, the notification
 * log, and the navigation out of a card into the ticket it is blocking.
 */
export function ApprovalInboxPanel() {
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const notifications = useNotificationStore((s) => s.notifications);
  const dismissNotification = useNotificationStore((s) => s.dismiss);
  const clearNotifications = useNotificationStore((s) => s.clear);

  // Every workspace: an approval is blocking a run, and hiding one because the
  // sidebar is pointed elsewhere is how a run sits parked for an afternoon.
  const approvalCount = usePendingApprovalCount("", inboxOpen);

  /** Inspect lands on the criteria for a human gate, on the diff otherwise. */
  const inspectApproval = (approval: Approval) => {
    if (!approval.ticket_id) return;
    navigateToTicket(approval.ticket_id, {
      tab: hasHumanCriteria(approval) ? "approvals" : "diff",
    });
    setInboxOpen(false);
  };

  // Escape and the overlay agree: both close the inbox. Above the early
  // return, because a hook that runs only while the panel is open changes
  // the hook count between renders.
  useDialogDismiss(inboxOpen ? () => setInboxOpen(false) : null);

  if (!inboxOpen) return null;

  const notificationCount = notifications.length;
  const totalCount = approvalCount + notificationCount;
  const empty = approvalCount === 0 && notificationCount === 0;

  return (
    <>
      {/* Presentational, so it stays out of the tab order — the panel beside it
          is what a keyboard operator lands on, and Escape is the way back out. */}
      <div className="inbox-overlay" role="presentation" onClick={() => setInboxOpen(false)} />
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
          {!empty ? (
            <>
              <section className="inbox-section" aria-label="Pending approvals">
                <div className="inbox-section-header">
                  <span className="inbox-section-title">Approvals</span>
                  <span className="count-pill">{approvalCount}</span>
                </div>
                <ApprovalsList workspaceSlug="" isActive={inboxOpen} onInspect={inspectApproval} />
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
                {notifications.length === 0 ? (
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
    </>
  );
}

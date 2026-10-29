import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useLocation } from "react-router-dom";

import { api } from "../api/client";
import { PANE_LABELS, PANE_ORDER } from "../lib/appTopbarConfig";
import { useNotificationStore } from "../state/notificationStore";
import { useUiStore } from "../state/uiStore";
import { ApprovalInboxPanel } from "./ApprovalInboxPanel";
import {
  TopbarDropdown,
  TopbarDropdownPaneRow,
} from "./TopbarDropdown";
import { Button } from "./ui/Button";

/**
 * The topbar's right end: the Console's pane picker there, and the Inbox on
 * every page. GitHub sync, Instances and Usage used to sit here on every page;
 * they moved to Settings, each ticket's header and the Machine page.
 */
export function AppTopbarActions() {
  const { pathname } = useLocation();
  const paneVisibility = useUiStore((s) => s.paneVisibility);
  const setPaneVisible = useUiStore((s) => s.setPaneVisible);
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
  });

  const notifications = useNotificationStore((s) => s.notifications);
  const notificationCount = notifications.length;

  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;
  const hiddenPaneCount = useMemo(
    () => Object.values(paneVisibility).filter((visible) => !visible).length,
    [paneVisibility],
  );
  const approvalCount = approvals.data?.length ?? 0;
  const failedCount = notifications.filter((n) => n.tone === "error").length;
  const otherCount = notificationCount - failedCount;
  // One chip per kind, so a pile of finished runs no longer reads as one big
  // number beside the two that need a person. One meaning per hue: red only
  // for failures, violet for an approval waiting on you, neutral for the rest.
  const inboxChips = [
    { tone: "bad", count: failedCount, glyph: "■" },
    { tone: "you", count: approvalCount, glyph: "◆" },
    { tone: "neutral", count: otherCount, glyph: "" },
  ].filter((chip) => chip.count > 0);
  const inboxTone = failedCount > 0 ? "bad" : approvalCount > 0 ? "you" : "neutral";
  const inboxNeedsAttention = inboxTone !== "neutral";
  const inboxLabel = [
    "Open approvals and notifications inbox",
    failedCount > 0 ? `${failedCount} failed` : null,
    approvalCount > 0 ? `${approvalCount} waiting on you` : null,
    otherCount > 0 ? `${otherCount} other ${otherCount === 1 ? "notification" : "notifications"}` : null,
  ]
    .filter(Boolean)
    .join(" — ");

  // The pane picker belongs to the Console; a ticket page has no panes to hide.
  const isIde = pathname === "/console";
  const panesLabel =
    hiddenPaneCount > 0 ? `Panes · ${hiddenPaneCount} hidden` : "Panes";

  return (
    <>
      <div className="topbar-actions">
        {isIde ? (
          <div className="topbar-actions-ide">
            <TopbarDropdown label={panesLabel} align="right">
              {PANE_ORDER.map((pane) => (
                <TopbarDropdownPaneRow
                  key={pane}
                  label={PANE_LABELS[pane]}
                  visible={paneVisibility[pane]}
                  disabled={visiblePaneCount <= 1}
                  onChange={(next) => {
                    if (!next && visiblePaneCount <= 1) return;
                    setPaneVisible(pane, next);
                  }}
                />
              ))}
            </TopbarDropdown>
          </div>
        ) : null}
        <div className="topbar-actions-core">
          <Button
            variant="secondary"
            className={`topbar-action-btn topbar-action-btn--strong topbar-action-btn--phone-icon${inboxNeedsAttention && !inboxOpen ? ` approvals-btn-pending approvals-btn-pending--${inboxTone}` : ""}`}
            onClick={() => setInboxOpen(true)}
            aria-label={inboxLabel}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
              <path d="M22 12h-6l-2 3h-4l-2-3H2" />
              <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
            </svg>
            <span className="topbar-action-label">Inbox</span>
            {inboxChips.map((chip) => (
              <span
                key={chip.tone}
                className={`approvals-badge approvals-badge--${chip.tone}`}
                data-tone={chip.tone}
                aria-hidden
              >
                {chip.glyph ? <span className="approvals-badge-glyph">{chip.glyph}</span> : null}
                {chip.count}
              </span>
            ))}
          </Button>
        </div>
      </div>

      <ApprovalInboxPanel />
    </>
  );
}

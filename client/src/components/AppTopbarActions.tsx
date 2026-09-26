import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import { useAppPage } from "../lib/useAppNavigation";
import { PANE_LABELS, PANE_ORDER } from "../lib/appTopbarConfig";
import { useNotificationStore } from "../state/notificationStore";
import { useUiStore } from "../state/uiStore";
import { ApprovalInboxPanel } from "./ApprovalInboxPanel";
import {
  TopbarDropdown,
  TopbarDropdownPaneRow,
} from "./TopbarDropdown";
import { LocalInstancesModal } from "./LocalInstancesModal";
import { UsageModal } from "./UsageModal";

const USAGE_REFRESH_MS = 30 * 60_000;

export function AppTopbarActions() {
  const appPage = useAppPage();
  const paneVisibility = useUiStore((s) => s.paneVisibility);
  const setPaneVisible = useUiStore((s) => s.setPaneVisible);
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);

  const [usageOpen, setUsageOpen] = useState(false);
  const [instancesOpen, setInstancesOpen] = useState(false);
  // The snapshot is served from a short server-side cache so a page load never
  // blocks on the providers; the modal's Refresh button asks for live numbers.
  const forceUsageRefresh = useRef(false);
  const usage = useQuery({
    queryKey: ["usage"],
    queryFn: () => {
      const force = forceUsageRefresh.current;
      forceUsageRefresh.current = false;
      return api.usage(force);
    },
    refetchInterval: USAGE_REFRESH_MS,
    staleTime: USAGE_REFRESH_MS,
    refetchOnWindowFocus: false,
  });

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
  });

  const notificationCount = useNotificationStore((s) => s.notifications.length);

  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;
  const hiddenPaneCount = useMemo(
    () => Object.values(paneVisibility).filter((visible) => !visible).length,
    [paneVisibility],
  );
  const approvalCount = approvals.data?.length ?? 0;
  const inboxCount = approvalCount + notificationCount;
  const inboxNeedsAttention = approvalCount > 0 || notificationCount > 0;

  const isIde = appPage === "dashboard";
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
          <button
            type="button"
            className="btn-secondary topbar-action-btn"
            onClick={() => setInstancesOpen(true)}
            aria-label="Open local instances: branch servers and clients on their own ports"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--bll)" strokeWidth="1.8" aria-hidden>
              <rect x="3" y="4" width="18" height="7" rx="1.5" />
              <rect x="3" y="13" width="18" height="7" rx="1.5" />
              <path d="M7 7.5h.01M7 16.5h.01" />
            </svg>
            Instances
          </button>
          <button
            type="button"
            className={`btn-secondary topbar-action-btn${usage.data?.near_limit && !usageOpen ? " usage-btn-warning" : ""}`}
            onClick={() => setUsageOpen(true)}
            aria-label={
              usage.data?.near_limit
                ? "Usage limits are getting close — open usage details"
                : "Open Claude and Cursor usage"
            }
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--aml)" strokeWidth="1.8" aria-hidden>
              <path d="M12 9v4M12 17h.01" />
              <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            </svg>
            Usage
            {usage.data?.near_limit ? (
              <span className="usage-alert-badge" aria-hidden="true">
                !
              </span>
            ) : null}
          </button>
          <button
            type="button"
            className={`btn-secondary topbar-action-btn topbar-action-btn--strong${inboxNeedsAttention && !inboxOpen ? " approvals-btn-pending" : ""}`}
            onClick={() => setInboxOpen(true)}
            aria-label="Open approvals and notifications inbox"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--rdl)" strokeWidth="1.9" aria-hidden>
              <path d="M22 12h-6l-2 3h-4l-2-3H2" />
              <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
            </svg>
            Inbox
            <span
              className="approvals-badge"
              style={{
                minWidth: 19,
                height: 19,
                padding: "0 5px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: inboxCount === 0 ? "var(--grn)" : approvalCount > 0 ? "var(--red)" : "var(--aml)",
                color: "#fff",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 10,
                fontFamily: "var(--mono)",
              }}
            >
              {inboxCount}
            </span>
          </button>
        </div>
      </div>

      <LocalInstancesModal open={instancesOpen} onClose={() => setInstancesOpen(false)} />
      <UsageModal
        open={usageOpen}
        snapshot={usage.data}
        isLoading={usage.isFetching}
        error={usage.error}
        onClose={() => setUsageOpen(false)}
        onRefresh={() => {
          forceUsageRefresh.current = true;
          void usage.refetch();
        }}
      />

      <ApprovalInboxPanel />
    </>
  );
}

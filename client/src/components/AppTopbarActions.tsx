import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import { useAppPage } from "../lib/useAppNavigation";
import { PANE_LABELS, PANE_ORDER } from "../lib/appTopbarConfig";
import { useNotificationStore } from "../state/notificationStore";
import { useUiStore } from "../state/uiStore";
import { ApprovalInboxPanel } from "./ApprovalInboxPanel";
import { OverflowMenu, OverflowMenuItem } from "./OverflowMenu";
import {
  TopbarDropdown,
  TopbarDropdownPaneRow,
} from "./TopbarDropdown";
import { GithubSyncModal } from "./GithubSyncModal";
import { LocalInstancesModal } from "./LocalInstancesModal";
import { UsageModal } from "./UsageModal";
import { Button } from "./ui/Button";

const USAGE_REFRESH_MS = 30 * 60_000;

// Each action shows in two places — inline, and in the phone-width overflow
// menu — so its icon is drawn once here.
function GithubSyncIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--bll)" strokeWidth="1.8" aria-hidden>
      <path d="M21 12a9 9 0 0 1-15.5 6.2M3 12a9 9 0 0 1 15.5-6.2" />
      <path d="M21 4v5h-5M3 20v-5h5" />
    </svg>
  );
}

function InstancesIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--bll)" strokeWidth="1.8" aria-hidden>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </svg>
  );
}

function UsageIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--aml)" strokeWidth="1.8" aria-hidden>
      <path d="M12 9v4M12 17h.01" />
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    </svg>
  );
}

export function AppTopbarActions() {
  const appPage = useAppPage();
  const paneVisibility = useUiStore((s) => s.paneVisibility);
  const setPaneVisible = useUiStore((s) => s.setPaneVisible);
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);

  const [usageOpen, setUsageOpen] = useState(false);
  const [instancesOpen, setInstancesOpen] = useState(false);
  const [githubOpen, setGithubOpen] = useState(false);
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

  const notifications = useNotificationStore((s) => s.notifications);
  const notificationCount = notifications.length;

  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;
  const hiddenPaneCount = useMemo(
    () => Object.values(paneVisibility).filter((visible) => !visible).length,
    [paneVisibility],
  );
  const usageNearLimit = Boolean(usage.data?.near_limit);
  const approvalCount = approvals.data?.length ?? 0;
  const inboxCount = approvalCount + notificationCount;
  const failedCount = notifications.filter((n) => n.tone === "error").length;
  // One meaning per hue: red only when something failed, violet when an
  // approval is waiting on a person, a neutral count for everything else. A
  // badge that was always red (or green at zero) taught people to ignore it.
  const inboxTone = failedCount > 0 ? "bad" : approvalCount > 0 ? "you" : "neutral";
  const inboxNeedsAttention = inboxTone !== "neutral";
  const inboxLabel = [
    "Open approvals and notifications inbox",
    failedCount > 0 ? `${failedCount} failed` : null,
    approvalCount > 0 ? `${approvalCount} waiting on you` : null,
  ]
    .filter(Boolean)
    .join(" — ");

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
          <Button
            variant="secondary"
            className="topbar-action-btn topbar-action-btn--overflowable"
            onClick={() => setGithubOpen(true)}
            aria-label="Sync tickets with GitHub issues"
          >
            <GithubSyncIcon />
            GitHub
          </Button>
          <Button
            variant="secondary"
            className="topbar-action-btn topbar-action-btn--overflowable"
            onClick={() => setInstancesOpen(true)}
            aria-label="Open local instances: branch servers and clients on their own ports"
          >
            <InstancesIcon />
            Instances
          </Button>
          <Button
            variant="secondary"
            className={`topbar-action-btn topbar-action-btn--overflowable${usageNearLimit && !usageOpen ? " usage-btn-warning" : ""}`}
            onClick={() => setUsageOpen(true)}
            aria-label={
              usageNearLimit
                ? "Usage limits are getting close — open usage details"
                : "Open Claude and Cursor usage"
            }
          >
            <UsageIcon />
            Usage
            {usageNearLimit ? (
              <span className="usage-alert-badge" aria-hidden="true">
                !
              </span>
            ) : null}
          </Button>
          {/* Phone width only (CSS): the three actions above fold in here, so
              the row keeps the workspace picker and the inbox on screen. */}
          <div className={`topbar-overflow${usageNearLimit && !usageOpen ? " topbar-overflow--warning" : ""}`}>
            <OverflowMenu
              label={usageNearLimit ? "More actions — usage limits are getting close" : "More actions"}
            >
              <OverflowMenuItem onSelect={() => setGithubOpen(true)}>
                <GithubSyncIcon />
                Sync with GitHub
              </OverflowMenuItem>
              <OverflowMenuItem onSelect={() => setInstancesOpen(true)}>
                <InstancesIcon />
                Local instances
              </OverflowMenuItem>
              <OverflowMenuItem onSelect={() => setUsageOpen(true)}>
                <UsageIcon />
                {usageNearLimit ? "Usage — limits getting close" : "Usage"}
              </OverflowMenuItem>
            </OverflowMenu>
          </div>
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
            <span className={`approvals-badge approvals-badge--${inboxTone}`} data-tone={inboxTone}>
              {inboxCount}
            </span>
          </Button>
        </div>
      </div>

      <GithubSyncModal open={githubOpen} onClose={() => setGithubOpen(false)} />
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

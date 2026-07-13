import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import { useAppPage } from "../lib/useAppNavigation";
import { PANE_LABELS, PANE_ORDER } from "../lib/appTopbarConfig";
import { useUiStore } from "../state/uiStore";
import { ApprovalInboxPanel } from "./ApprovalInboxPanel";
import { AppTopbarToolMenu } from "./AppTopbarToolMenu";
import { MemorySetupModal } from "./MemorySetupModal";
import {
  TopbarDropdown,
  TopbarDropdownPaneRow,
} from "./TopbarDropdown";
import { UsageModal } from "./UsageModal";

const USAGE_REFRESH_MS = 30 * 60_000;

export function AppTopbarActions() {
  const qc = useQueryClient();
  const appPage = useAppPage();
  const paneVisibility = useUiStore((s) => s.paneVisibility);
  const setPaneVisible = useUiStore((s) => s.setPaneVisible);
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);

  const [memoryOpen, setMemoryOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const usage = useQuery({
    queryKey: ["usage"],
    queryFn: api.usage,
    refetchInterval: USAGE_REFRESH_MS,
    staleTime: USAGE_REFRESH_MS,
    refetchOnWindowFocus: false,
  });

  const memoryConfig = useQuery({
    queryKey: ["memory-config"],
    queryFn: api.memoryConfig,
    enabled: memoryOpen,
  });

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
  });

  const setMemoryConfig = useMutation({
    mutationFn: api.setMemoryConfig,
    onSuccess: (data) => {
      qc.setQueryData(["memory-config"], data);
    },
  });

  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;
  const hiddenPaneCount = useMemo(
    () => Object.values(paneVisibility).filter((visible) => !visible).length,
    [paneVisibility],
  );
  const approvalCount = approvals.data?.length ?? 0;

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
            <button type="button" className="btn-secondary topbar-action-btn" onClick={() => setMemoryOpen(true)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              Memory
            </button>
          </div>
        ) : null}
        <div className="topbar-actions-core">
          <AppTopbarToolMenu />
          <button
            type="button"
            className={`btn-secondary topbar-action-btn usage-btn${usage.data?.near_limit && !usageOpen ? " usage-btn-warning" : ""}`}
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
            className={`btn-secondary topbar-action-btn topbar-action-btn--strong${approvalCount > 0 && !inboxOpen ? " approvals-btn-pending" : ""}`}
            onClick={() => setInboxOpen(true)}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--rdl)" strokeWidth="1.9" aria-hidden>
              <path d="M22 12h-6l-2 3h-4l-2-3H2" />
              <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
            </svg>
            Approvals
            <span
              className="approvals-badge"
              style={{
                minWidth: 19,
                height: 19,
                padding: "0 5px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: approvalCount === 0 ? "var(--grn)" : "var(--red)",
                color: "#fff",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 10,
                fontFamily: "var(--mono)",
              }}
            >
              {approvalCount}
            </span>
          </button>
        </div>
      </div>

      <MemorySetupModal
        open={memoryOpen}
        data={memoryConfig.data}
        isLoading={memoryConfig.isLoading}
        isSaving={setMemoryConfig.isPending}
        errorMessage={
          setMemoryConfig.error
            ? (() => {
                try {
                  const parsed = JSON.parse(setMemoryConfig.error.message) as { detail?: string };
                  return parsed.detail ?? setMemoryConfig.error.message;
                } catch {
                  return setMemoryConfig.error.message;
                }
              })()
            : undefined
        }
        onClose={() => {
          if (setMemoryConfig.isPending) return;
          setMemoryConfig.reset();
          setMemoryOpen(false);
        }}
        onRefresh={() => void memoryConfig.refetch()}
        onSave={async (config) => {
          await setMemoryConfig.mutateAsync(config);
        }}
      />

      <UsageModal
        open={usageOpen}
        snapshot={usage.data}
        isLoading={usage.isFetching}
        error={usage.error}
        onClose={() => setUsageOpen(false)}
        onRefresh={() => void usage.refetch()}
      />

      <ApprovalInboxPanel />
    </>
  );
}

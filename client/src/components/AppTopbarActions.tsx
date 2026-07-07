import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import { PANE_LABELS, PANE_ORDER } from "../lib/appTopbarConfig";
import { useUiStore } from "../state/uiStore";
import { ApprovalInboxPanel } from "./ApprovalInboxPanel";
import { AppTopbarToolMenu } from "./AppTopbarToolMenu";
import { MemorySetupModal } from "./MemorySetupModal";
import { SettingsModal } from "./SettingsModal";
import {
  TopbarDropdown,
  TopbarDropdownPaneRow,
} from "./TopbarDropdown";
import { UsageModal } from "./UsageModal";

export function AppTopbarActions() {
  const qc = useQueryClient();
  const appPage = useUiStore((s) => s.appPage);
  const paneVisibility = useUiStore((s) => s.paneVisibility);
  const setPaneVisible = useUiStore((s) => s.setPaneVisible);
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const workspace = useUiStore((s) => s.workspace);
  const editorWorkspace = useUiStore((s) => s.editorWorkspace);
  const queueWorkspaceSlug = useUiStore((s) => s.queueWorkspaceSlug);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [settingsWorkspaceSlug, setSettingsWorkspaceSlug] = useState("loregarden");

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });

  const runtimeOptions = useQuery({
    queryKey: ["runtime-options"],
    queryFn: api.runtimeOptions,
  });

  const usage = useQuery({
    queryKey: ["usage"],
    queryFn: api.usage,
    refetchInterval: usageOpen ? 60_000 : 5 * 60_000,
    staleTime: 30_000,
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

  const setRuntime = useMutation({
    mutationFn: ({
      slug,
      runtime,
    }: {
      slug: string;
      runtime: {
        cli_adapter: string;
        claude_model: string;
        cursor_model: string;
        lmstudio_base_url: string;
        lmstudio_model: string;
      };
    }) => api.setWorkspaceRuntime(slug, runtime),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-runtime", vars.slug] });
    },
  });

  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;
  const hiddenPaneCount = useMemo(
    () => Object.values(paneVisibility).filter((visible) => !visible).length,
    [paneVisibility],
  );
  const approvalCount = approvals.data?.length ?? 0;

  const defaultSettingsSlug = useMemo(() => {
    if (workspace && workspace !== "all") return workspace;
    if (appPage === "editor" && editorWorkspace) return editorWorkspace;
    if (appPage === "queue" && queueWorkspaceSlug) return queueWorkspaceSlug;
    return workspaces.data?.[0]?.slug ?? "loregarden";
  }, [workspace, appPage, editorWorkspace, queueWorkspaceSlug, workspaces.data]);

  const openSettings = () => {
    setSettingsWorkspaceSlug(defaultSettingsSlug);
    setSettingsOpen(true);
  };

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
            <button type="button" className="btn-secondary" onClick={() => setMemoryOpen(true)}>
              Memory
            </button>
            <button type="button" className="btn-secondary" onClick={openSettings}>
              Settings
            </button>
          </div>
        ) : null}
        <div className="topbar-actions-core">
          <AppTopbarToolMenu />
          <button
            type="button"
            className={`btn-secondary usage-btn${usage.data?.near_limit && !usageOpen ? " usage-btn-warning" : ""}`}
            onClick={() => setUsageOpen(true)}
            aria-label={
              usage.data?.near_limit
                ? "Usage limits are getting close — open usage details"
                : "Open Claude and Cursor usage"
            }
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            Usage
            {usage.data?.near_limit ? (
              <span className="usage-alert-badge" aria-hidden="true">
                !
              </span>
            ) : null}
          </button>
          <button
            type="button"
            className={`btn-secondary${approvalCount > 0 && !inboxOpen ? " approvals-btn-pending" : ""}`}
            onClick={() => setInboxOpen(true)}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
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

      <SettingsModal
        open={settingsOpen}
        workspaceSlug={settingsWorkspaceSlug}
        workspaces={workspaces.data ?? []}
        runtimeOptions={runtimeOptions.data}
        isSaving={setRuntime.isPending}
        onClose={() => setSettingsOpen(false)}
        onWorkspaceChange={setSettingsWorkspaceSlug}
        onSave={async (slug, runtime) => {
          await setRuntime.mutateAsync({ slug, runtime });
        }}
      />

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

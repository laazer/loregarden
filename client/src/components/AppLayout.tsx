import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";

import { api } from "../api/client";
import { useAppPage } from "../lib/useAppNavigation";
import { useUiStore } from "../state/uiStore";
import { AppIconRail } from "./AppIconRail";
import { AppTopbar } from "./AppTopbar";
import { AppUtilityDock } from "./AppUtilityDock";
import { SettingsModal } from "./SettingsModal";
import { ToastHost } from "./ToastHost";
import { TopbarPageSlotProvider } from "./TopbarPageSlot";

export function AppLayout({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const appPage = useAppPage();
  const workspace = useUiStore((s) => s.workspace);
  const editorWorkspace = useUiStore((s) => s.editorWorkspace);
  const queueWorkspaceSlug = useUiStore((s) => s.queueWorkspaceSlug);
  const branchTriageWorkspaceSlug = useUiStore((s) => s.branchTriageWorkspaceSlug);
  const utilityDockEdge = useUiStore((s) => s.utilityDockEdge);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsWorkspaceSlug, setSettingsWorkspaceSlug] = useState("loregarden");

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const runtimeOptions = useQuery({
    queryKey: ["runtime-options", workspace],
    queryFn: () => api.runtimeOptions({ workspace }),
  });

  const setRuntime = useMutation({
    meta: { errorTitle: "Save runtime settings" },
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

  const defaultSettingsSlug = useMemo(() => {
    if (workspace && workspace !== "all") return workspace;
    if (appPage === "editor" && editorWorkspace) return editorWorkspace;
    if (appPage === "queue" && queueWorkspaceSlug) return queueWorkspaceSlug;
    if (appPage === "branch-triage" && branchTriageWorkspaceSlug) return branchTriageWorkspaceSlug;
    return workspaces.data?.[0]?.slug ?? "loregarden";
  }, [workspace, appPage, editorWorkspace, queueWorkspaceSlug, branchTriageWorkspaceSlug, workspaces.data]);

  const openSettings = () => {
    setSettingsWorkspaceSlug(defaultSettingsSlug);
    setSettingsOpen(true);
  };

  const showUtilityDock = appPage !== "chat";

  return (
    <div className="app-frame">
      <div className="app-ambient" aria-hidden />
      <AppIconRail onOpenSettings={openSettings} />
      <TopbarPageSlotProvider>
        <div className="app-main">
          <AppTopbar />
          <div className={`app-body app-body--dock-${utilityDockEdge}`}>
            <div className="screen-area">{children}</div>
            {showUtilityDock ? <AppUtilityDock /> : null}
          </div>
        </div>
      </TopbarPageSlotProvider>

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

      <ToastHost />
    </div>
  );
}

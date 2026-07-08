import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";

import { api } from "../api/client";
import { useAppPage } from "../lib/useAppNavigation";
import { useUiStore } from "../state/uiStore";
import { AppIconRail } from "./AppIconRail";
import { SettingsModal } from "./SettingsModal";

export function AppLayout({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const appPage = useAppPage();
  const workspace = useUiStore((s) => s.workspace);
  const editorWorkspace = useUiStore((s) => s.editorWorkspace);
  const queueWorkspaceSlug = useUiStore((s) => s.queueWorkspaceSlug);
  const branchTriageWorkspaceSlug = useUiStore((s) => s.branchTriageWorkspaceSlug);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsWorkspaceSlug, setSettingsWorkspaceSlug] = useState("loregarden");

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const runtimeOptions = useQuery({
    queryKey: ["runtime-options"],
    queryFn: api.runtimeOptions,
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

  return (
    <div className="app-frame">
      <div className="app-ambient" aria-hidden />
      <AppIconRail onOpenSettings={openSettings} />
      <div className="screen-area">{children}</div>

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
    </div>
  );
}

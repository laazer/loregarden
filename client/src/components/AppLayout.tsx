import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";

import { api } from "../api/client";
import { useAppPage } from "../lib/useAppNavigation";
import { SidebarWorkspaceProvider } from "../state/SidebarWorkspaceContext";
import { useUiStore } from "../state/uiStore";
import { AppSidebar } from "./AppSidebar";
import { AppTopbar } from "./AppTopbar";
import { AppUtilityDock } from "./AppUtilityDock";
import { SettingsModal } from "./SettingsModal";
import { QueueNotificationsHost } from "./QueueNotificationsHost";
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
        codex_model?: string;
        lmstudio_base_url: string;
        lmstudio_model: string;
      };
    }) => api.setWorkspaceRuntime(slug, runtime),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-runtime", vars.slug] });
    },
  });

  /**
   * The concrete workspace the *settings modal* opens on. `uiStore.workspace` is
   * `"all"` until one is chosen, so this falls through to whichever slug the
   * current page is scoped to, and finally to the first workspace.
   *
   * Route-dependent by design, and therefore not what the sidebar uses — see
   * `sidebarWorkspaceSlug` below.
   */
  /**
   * Whether the user has actually chosen a workspace. `uiStore.workspace` holds
   * `"all"` until the Dashboard picker moves it, and both resolutions below turn
   * on that one question — spelled twice, it is two places to fix the day the
   * sentinel changes.
   */
  const workspaceChosen = Boolean(workspace) && workspace !== "all";

  const resolvedWorkspaceSlug = useMemo(() => {
    if (workspaceChosen) return workspace;
    if (appPage === "editor" && editorWorkspace) return editorWorkspace;
    if (appPage === "queue" && queueWorkspaceSlug) return queueWorkspaceSlug;
    if (appPage === "branch-triage" && branchTriageWorkspaceSlug) return branchTriageWorkspaceSlug;
    return workspaces.data?.[0]?.slug ?? "";
  }, [
    workspace,
    workspaceChosen,
    appPage,
    editorWorkspace,
    queueWorkspaceSlug,
    branchTriageWorkspaceSlug,
    workspaces.data,
  ]);

  /**
   * The workspace the *sidebar* shows, which deliberately does not follow the
   * route. `resolvedWorkspaceSlug` falls through to per-page slugs, so walking
   * `/queue` → `/console` would swap the entire tab set underneath the user —
   * chrome does not re-arrange itself because a page changed. Only an explicit
   * choice moves it; otherwise it shows the first workspace.
   *
   * Seeding follows the resolved slug, not the choice. `uiStore.workspace` stays
   * `"all"` until the Dashboard picker moves it, and "All workspaces" is a place
   * a user may legitimately sit forever — gating the seed on an explicit choice
   * leaves a fresh install with no navigation links at all. The first workspace
   * is stable across navigation and one the user demonstrably has, so its
   * default pins belong there.
   */
  const sidebarWorkspaceSlug = workspaceChosen ? workspace : workspaces.data?.[0]?.slug ?? "";
  /**
   * Whether that slug is this chrome's answer or just its state so far. Until
   * the workspace list lands, an empty slug means "still asking" — and a route
   * that renders "pick a workspace" on it says so on a page with no picker.
   */
  const sidebarWorkspaceResolved = workspaceChosen || !workspaces.isPending;

  const openSettings = () => {
    setSettingsWorkspaceSlug(resolvedWorkspaceSlug || "loregarden");
    setSettingsOpen(true);
  };

  return (
    <SidebarWorkspaceProvider slug={sidebarWorkspaceSlug} isResolved={sidebarWorkspaceResolved}>
      <div className="app-frame">
        <div className="app-ambient" aria-hidden />
        <AppSidebar workspaceSlug={sidebarWorkspaceSlug} onOpenSettings={openSettings} />
        <TopbarPageSlotProvider>
          <div className="app-main">
            <AppTopbar />
            <div className={`app-body app-body--dock-${utilityDockEdge}`}>
              <div className="screen-area">{children}</div>
              {/* Every screen, chat included: the bar carries the shell and the
                  dock control, which are screen-level tools rather than chat
                  ones. The bar itself drops its composer where the page has one
                  (see `composedOnScreen` in useActiveChatSession). */}
              <AppUtilityDock />
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
        <QueueNotificationsHost />
      </div>
    </SidebarWorkspaceProvider>
  );
}

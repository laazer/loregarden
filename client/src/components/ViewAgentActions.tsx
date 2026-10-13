import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { createViewContending } from "../hooks/useSidebarTabs";
import { useAgentAction } from "../lib/agentActions/useAgentAction";
import { viewPath } from "../lib/appNavigation";
import { fetchView, viewsKeys } from "../lib/viewsApi";
import { useSidebarWorkspaceSlug } from "../state/SidebarWorkspaceContext";
import { describeError } from "../state/toastStore";
import { useUiStore } from "../state/uiStore";

/**
 * Lets agents add a sidebar tab (a flex-grid or canvas view) and show one.
 *
 * Mounted inside `AppLayout`'s `SidebarWorkspaceProvider`, because a view route
 * resolves its view against the sidebar's workspace: a view in any other
 * workspace is a 404 there. So when the agent names another workspace, the
 * sidebar is switched to it first — the same explicit choice the Dashboard
 * picker makes, announced to the operator by `AgentActionHost`'s toast.
 * Renders nothing itself.
 */
export function ViewAgentActions() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sidebarSlug = useSidebarWorkspaceSlug();
  const setWorkspace = useUiStore((s) => s.setWorkspace);

  const show = (slug: string, viewId: string) => {
    if (slug !== sidebarSlug) setWorkspace(slug);
    const path = viewPath(viewId);
    navigate(path);
    return path;
  };

  useAgentAction("view.create", async ({ workspace_slug, title, layout }) => {
    // Not optimistic, for the reason the New View form is not: the id is the
    // server's, and navigating before it exists lands on the not-found state.
    const view = await createViewContending(workspace_slug, { title, icon: "", layout });
    // The view and its sidebar entry are one transaction read back as two queries.
    void queryClient.invalidateQueries({ queryKey: viewsKeys.views(workspace_slug) });
    void queryClient.invalidateQueries({ queryKey: viewsKeys.sidebarEntries(workspace_slug) });
    return { view_id: view.id, title: view.title, kind: view.kind, path: show(workspace_slug, view.id) };
  });

  useAgentAction("view.open", async ({ workspace_slug, view_id }) => {
    // Checked before navigating, so a wrong id fails back to the agent by name
    // instead of leaving the operator on a not-found page the agent never sees.
    const view = await fetchView(workspace_slug, view_id).catch((error: unknown) => {
      throw new Error(
        `view ${view_id} in workspace ${workspace_slug}: ${describeError(error, "could not be read")}`,
      );
    });
    return { view_id: view.id, title: view.title, path: show(workspace_slug, view.id) };
  });

  return null;
}

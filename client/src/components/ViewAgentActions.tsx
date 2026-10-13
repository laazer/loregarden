import { MutationObserver, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { createViewContending } from "../hooks/useSidebarTabs";
import { storeWrittenView, viewWriteScope } from "../hooks/useViewLayoutEdit";
import { useAgentAction } from "../lib/agentActions/useAgentAction";
import { viewPath } from "../lib/appNavigation";
import { fetchView, updateView, viewsKeys, type ViewLayout, type ViewPatch } from "../lib/viewsApi";
import { useSidebarWorkspaceSlug } from "../state/SidebarWorkspaceContext";
import { describeError } from "../state/toastStore";
import { useUiStore } from "../state/uiStore";

/**
 * A short content hash of a layout, which an agent hands back to `view.update`
 * to say which layout its edit was made from.
 *
 * The server serializes a layout in one key order, so equal layouts read back
 * as equal strings. `updated_at` cannot do this job: every pan moves it.
 * FNV-1a, 32-bit: this tells an agent its read is stale; it is not a
 * security boundary.
 */
export function layoutRevision(layout: ViewLayout): string {
  let hash = 0x811c9dc5;
  for (const char of JSON.stringify(layout)) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * Lets agents add a sidebar tab (a flex-grid or canvas view), show one, and edit one.
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
    return {
      view_id: view.id,
      title: view.title,
      kind: view.kind,
      revision: layoutRevision(view.layout),
      path: show(workspace_slug, view.id),
    };
  });

  useAgentAction("view.open", async ({ workspace_slug, view_id }) => {
    // Checked before navigating, so a wrong id fails back to the agent by name
    // instead of leaving the operator on a not-found page the agent never sees.
    const view = await fetchView(workspace_slug, view_id).catch((error: unknown) => {
      throw new Error(
        `view ${view_id} in workspace ${workspace_slug}: ${describeError(error, "could not be read")}`,
      );
    });
    return {
      view_id: view.id,
      title: view.title,
      layout: view.layout,
      revision: layoutRevision(view.layout),
      path: show(workspace_slug, view.id),
    };
  });

  useAgentAction("view.update", async ({ workspace_slug, view_id, title, layout, base_revision }) => {
    const patch: ViewPatch = {};
    if (title !== undefined) patch.title = title;
    if (layout !== undefined) patch.layout = layout;
    // Queued in the same scope as the page's own layout writes, so an edit the
    // operator has in flight lands first and this one is compared against it.
    const record = await new MutationObserver(queryClient, {
      scope: { id: viewWriteScope(workspace_slug, view_id) },
      // AgentActionHost already tells the operator; a second toast says it twice.
      meta: { suppressErrorToast: true },
      mutationFn: async () => {
        if (layout !== undefined) {
          // The server's layout, not the cache's: the edit may have come from another tab.
          const current = await fetchView(workspace_slug, view_id);
          const revision = layoutRevision(current.layout);
          if (revision !== base_revision) {
            throw new Error(
              `view ${view_id} changed since revision ${base_revision} (now ${revision}); ` +
                "read it again with view.open and re-apply the edit",
            );
          }
        }
        return updateView(workspace_slug, view_id, patch);
      },
    }).mutate();
    storeWrittenView(queryClient, workspace_slug, view_id, record);
    return {
      view_id: record.id,
      title: record.title,
      revision: layoutRevision(record.layout),
      path: show(workspace_slug, record.id),
    };
  });

  return null;
}

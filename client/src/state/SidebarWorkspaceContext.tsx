/**
 * The concrete workspace slug the chrome is showing.
 *
 * `uiStore.workspace` is `"all"` until the Dashboard picker moves it, and every
 * view route 404s on that, so `AppLayout` resolves a real slug for the sidebar.
 * The `/view/:viewId` route is a sibling of the sidebar in a flat route table
 * with no workspace in it, and it has to read the *same* slug — resolving it a
 * second time would mean two answers that drift, plus a second `workspaces`
 * query on a page that has no use for one.
 *
 * The slug alone cannot say why it is empty. `""` is both "the workspace list is
 * still loading" and "there is no workspace to show", and a consumer that
 * collapses them tells a user reloading a deep link to pick a workspace on a
 * page with no picker on it — permanently, if they do not think to navigate away
 * first. So resolution is published beside the slug: an unresolved empty slug is
 * a wait, a resolved one is an answer.
 *
 * The default is an unresolved empty string: a consumer outside the provider has
 * no workspace and no reason to believe that is final.
 */

import { createContext, useContext, useMemo, type ReactNode } from "react";

export interface SidebarWorkspace {
  /** A concrete slug, or `""` when there is none — never `"all"`. */
  slug: string;
  /** Whether the chrome has finished deciding: `false` means "not yet". */
  isResolved: boolean;
}

const SidebarWorkspaceContext = createContext<SidebarWorkspace>({ slug: "", isResolved: false });

export function SidebarWorkspaceProvider({
  slug,
  isResolved = true,
  children,
}: {
  slug: string;
  /**
   * Defaults to resolved, so a caller with a slug in hand — a test, a shell
   * that has one — does not have to say so.
   */
  isResolved?: boolean;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ slug, isResolved }), [slug, isResolved]);
  return <SidebarWorkspaceContext.Provider value={value}>{children}</SidebarWorkspaceContext.Provider>;
}

export function useSidebarWorkspace(): SidebarWorkspace {
  return useContext(SidebarWorkspaceContext);
}

export function useSidebarWorkspaceSlug(): string {
  return useContext(SidebarWorkspaceContext).slug;
}

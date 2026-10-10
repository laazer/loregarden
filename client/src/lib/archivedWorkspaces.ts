import type { TicketTreeNode, WorkspaceSummary } from "../api/types";

/** Slugs of the workspaces the operator archived on the Workspaces page. */
export function archivedWorkspaceSlugs(workspaces: readonly WorkspaceSummary[]): Set<string> {
  return new Set(workspaces.filter((w) => w.archived_at).map((w) => w.slug));
}

/**
 * The tree without items that belong to an archived workspace, at any depth.
 * Workspaceless items (initiatives) stay. Returns `nodes` itself when nothing
 * is archived, so a caller memoising on it keeps a stable identity.
 */
export function withoutArchivedWorkspaces(nodes: TicketTreeNode[], archived: ReadonlySet<string>): TicketTreeNode[] {
  if (archived.size === 0) return nodes;
  return nodes
    .filter((node) => !node.workspace_slug || !archived.has(node.workspace_slug))
    .map((node) => ({ ...node, children: withoutArchivedWorkspaces(node.children, archived) }));
}

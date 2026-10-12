/**
 * Which Baxter chat archive the chat page is on.
 *
 * Most conversations belong to one workspace. Initiatives span workspaces, so
 * chat also has *global* threads that answer across every workspace, either
 * unfocused or focused on one initiative. The server keeps three separate
 * archives for these (`/workspaces/{slug}/baxter-chat`, `/baxter-chat/global`,
 * and `/baxter-chat/global?initiative_id=`).
 *
 * The scope is stored and keyed as one string: a workspace's key is its slug,
 * so state persisted before global chat existed still reads as a workspace.
 * The two global forms start with `@`, which no slug made in the app does
 * (`slugify` keeps only `[a-z0-9-]`).
 */

export type ChatScope =
  | { kind: "workspace"; slug: string }
  | { kind: "global"; initiativeId: string | null };

export const GLOBAL_CHAT_SCOPE = "@global";
const INITIATIVE_SCOPE_PREFIX = "@initiative:";

export function initiativeChatScope(initiativeId: string): string {
  return `${INITIATIVE_SCOPE_PREFIX}${initiativeId}`;
}

/** `""` (nothing resolved yet) is `null`; every other key is a scope. */
export function parseChatScope(key: string): ChatScope | null {
  if (!key) return null;
  if (key === GLOBAL_CHAT_SCOPE) return { kind: "global", initiativeId: null };
  if (key.startsWith(INITIATIVE_SCOPE_PREFIX)) {
    const initiativeId = key.slice(INITIATIVE_SCOPE_PREFIX.length);
    return initiativeId ? { kind: "global", initiativeId } : null;
  }
  return { kind: "workspace", slug: key };
}

export function isGlobalChatScope(key: string): boolean {
  return parseChatScope(key)?.kind === "global";
}

/** The workspace a scope is confined to, or `""` for a global scope. */
export function chatScopeWorkspaceSlug(key: string): string {
  const scope = parseChatScope(key);
  return scope?.kind === "workspace" ? scope.slug : "";
}

/** The initiative a global scope is focused on, or `null`. */
export function chatScopeInitiativeId(key: string): string | null {
  const scope = parseChatScope(key);
  return scope?.kind === "global" ? scope.initiativeId : null;
}

/** The archive's base path, e.g. `/api/baxter-chat/global/sessions`. */
export function baxterChatSessionsPath(key: string): string {
  const scope = parseChatScope(key);
  if (!scope) throw new Error("No chat scope selected");
  return scope.kind === "workspace"
    ? `/api/workspaces/${encodeURIComponent(scope.slug)}/baxter-chat/sessions`
    : "/api/baxter-chat/global/sessions";
}

/**
 * The query that selects a focused archive, for listing and creating only.
 * Reading a thread by id needs none: the id names it.
 */
export function baxterChatArchiveQuery(key: string): string {
  const initiativeId = chatScopeInitiativeId(key);
  return initiativeId ? `?initiative_id=${encodeURIComponent(initiativeId)}` : "";
}

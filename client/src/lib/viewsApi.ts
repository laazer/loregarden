/**
 * The view store's REST surface: composed views and the sidebar that ranks them.
 *
 * Two facts from the server shape everything a caller does here:
 *
 *   - **Ranking is relative, and shared.** Views and pinned pages sit in one
 *     ordering whose positions are not contiguous, so a reorder sends the
 *     complete permutation of both — never an index derived from a count.
 *   - **A view's sidebar entry is not deletable.** `deleteSidebarEntry` is the
 *     unpin path for built-in pages only; closing a view is `deleteView`, which
 *     drops the entry with it.
 */

import { request } from "../api/http";

/** The wire vocabulary; `Grid`/`Canvas` are display strings, not these. */
export type ViewKind = "flex_grid" | "canvas";

export type SidebarEntryKind = "page" | "view";

/**
 * The layout blob, validated server-side and modelled by the grid/canvas
 * tickets. The sidebar carries it without reading it.
 */
export type ViewLayout = Record<string, unknown>;

export interface ViewSummary {
  id: string;
  kind: ViewKind;
  title: string;
  icon: string;
  layout: ViewLayout;
  created_at: string;
  updated_at: string;
}

/**
 * The half an entry does not use comes back as an empty string, so a reader
 * never has to branch on the kind to know which field is meaningful.
 */
export interface SidebarEntry {
  id: string;
  position: number;
  entry_kind: SidebarEntryKind;
  page_key: string;
  view_id: string;
}

export interface ViewPatch {
  title?: string;
  icon?: string;
  layout?: ViewLayout;
}

export interface DeletedRef {
  deleted: string;
}

function workspacePath(slug: string, suffix: string): string {
  return `/api/workspaces/${encodeURIComponent(slug)}${suffix}`;
}

/** Every view in the workspace, in sidebar order. */
export function fetchViews(slug: string): Promise<ViewSummary[]> {
  return request<ViewSummary[]>(workspacePath(slug, "/views"));
}

/** The sidebar's ranked entries — pinned pages and view tabs in one list. */
export function fetchSidebarEntries(slug: string): Promise<SidebarEntry[]> {
  return request<SidebarEntry[]>(workspacePath(slug, "/sidebar-entries"));
}

/**
 * Pin a built-in page. Idempotent and race-safe: pinning one already pinned
 * returns its existing entry, still with a 201, so the status code says nothing
 * about whether a row was created.
 */
export function pinPage(slug: string, pageKey: string): Promise<SidebarEntry> {
  return request<SidebarEntry>(workspacePath(slug, "/sidebar-entries"), {
    method: "POST",
    body: JSON.stringify({ page_key: pageKey }),
  });
}

/** Unpin a built-in page. A view's entry is refused with a 400 — delete the view. */
export function unpinEntry(slug: string, entryId: string): Promise<DeletedRef> {
  return request<DeletedRef>(
    workspacePath(slug, `/sidebar-entries/${encodeURIComponent(entryId)}`),
    { method: "DELETE" },
  );
}

/**
 * Re-rank the sidebar. `entryIds` is the complete permutation across both
 * sections; a partial list, a repeat, or a foreign id is refused whole.
 */
export function reorderSidebarEntries(
  slug: string,
  entryIds: string[],
): Promise<SidebarEntry[]> {
  return request<SidebarEntry[]>(workspacePath(slug, "/sidebar-entries"), {
    method: "PATCH",
    body: JSON.stringify({ entry_ids: entryIds }),
  });
}

/** True-partial PATCH: an omitted field is left alone, not reset. */
export function updateView(
  slug: string,
  viewId: string,
  patch: ViewPatch,
): Promise<ViewSummary> {
  return request<ViewSummary>(workspacePath(slug, `/views/${encodeURIComponent(viewId)}`), {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** Delete a view and, with it, its sidebar entry. */
export function deleteView(slug: string, viewId: string): Promise<DeletedRef> {
  return request<DeletedRef>(workspacePath(slug, `/views/${encodeURIComponent(viewId)}`), {
    method: "DELETE",
  });
}

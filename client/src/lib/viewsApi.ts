/**
 * The view store's REST surface: composed views and the sidebar that ranks them.
 *
 * Two facts from the server shape everything a caller does here:
 *
 *   - **Ranking is relative, and shared.** The Pinned section and Tabs sit in
 *     one ordering whose positions are not contiguous, so a reorder sends the
 *     complete permutation of both — never an index derived from a count.
 *   - **A view's sidebar entry is not deletable.** Closing a view is
 *     `deleteView`, which drops the entry with it; `setEntryPinned` is what
 *     moves a tab between the two sections.
 *
 * The built-in pages have no wrapper here on purpose. The server still serves
 * `POST`/`DELETE /sidebar-entries` for pinned pages, but the sidebar's Tools
 * section is derived from the client's page catalog rather than stored, so
 * nothing in this app pins one.
 */

import { ApiError, request } from "../api/http";

/** The wire vocabulary; `Grid`/`Canvas` are display strings, not these. */
export type ViewKind = "flex_grid" | "canvas";

export type SidebarEntryKind = "page" | "view";

/**
 * The layout blob, validated server-side and modelled by the grid/canvas
 * tickets. The sidebar carries it without reading it.
 */
export type ViewLayout = Record<string, unknown>;

/**
 * Where the view was last looked at, validated server-side and stored in its own
 * column. `{}` means no stored position — a legal state, and what every view
 * composed before 480 holds. Read as an opaque object for the same reason the
 * layout is: `canvasViewport` owns turning it into pan and zoom, and does it
 * totally, so a record the server widens later cannot break a canvas.
 */
export type ViewViewport = Record<string, unknown>;

/** The body that stores one: three finite numbers, none of them optional. */
export interface ViewViewportPatch {
  pan_x: number;
  pan_y: number;
  zoom: number;
}

export interface ViewSummary {
  id: string;
  kind: ViewKind;
  title: string;
  icon: string;
  layout: ViewLayout;
  viewport: ViewViewport;
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
  /** Which section draws this tab: the Pinned one, or Tabs. */
  pinned: boolean;
}

export interface ViewPatch {
  title?: string;
  icon?: string;
  layout?: ViewLayout;
  /**
   * Independently settable: a patch carrying only this leaves the layout exactly
   * as it was, which is what lets a pan be written at gesture rate without
   * racing a deliberate layout edit through one field.
   */
  viewport?: ViewViewportPatch;
}

/**
 * The create body, which carries **no** top-level `kind`.
 *
 * The view's kind is the layout's discriminator tag, and `ViewCreate` is
 * `extra="forbid"` server-side — sending the kind the user just picked, next to
 * the layout it seeded, is a 422 with nothing on screen to explain it.
 */
export interface ViewCreate {
  title: string;
  icon: string;
  layout: ViewLayout;
}

export interface DeletedRef {
  deleted: string;
}

function workspacePath(slug: string, suffix: string): string {
  return `/api/workspaces/${encodeURIComponent(slug)}${suffix}`;
}

/**
 * The query keys the view store's caches live under, spelled once.
 *
 * Creating a view writes the view and its sidebar entry in one server-side
 * transaction, which the client reads as two queries — so the create path has to
 * invalidate both, under exactly the keys the sidebar reads them under. Two
 * spellings of one key is a cache that silently stops refreshing the day one of
 * them is edited.
 */
export const viewsKeys = {
  views: (slug: string) => ["views", slug] as const,
  sidebarEntries: (slug: string) => ["sidebar-entries", slug] as const,
  view: (slug: string, viewId: string) => ["view", slug, viewId] as const,
};

/**
 * A write that lost a race and is worth re-issuing.
 *
 * Realistic with two tabs open on one workspace. A 400 or 422 is "fix the
 * request" and re-sending it changes nothing, so only the 409 is retried.
 */
export function isContention(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/** Every view in the workspace, in sidebar order. */
export function fetchViews(slug: string): Promise<ViewSummary[]> {
  return request<ViewSummary[]>(workspacePath(slug, "/views"));
}

/** One view by id. A deleted or unknown id is a 404, which the route renders. */
export function fetchView(slug: string, viewId: string): Promise<ViewSummary> {
  return request<ViewSummary>(workspacePath(slug, `/views/${encodeURIComponent(viewId)}`));
}

/**
 * Create a view, and with it the sidebar entry the server appends in the same
 * transaction. The body is built field by field rather than spread, because
 * `extra="forbid"` turns one stray key into a 422.
 */
export function createView(slug: string, body: ViewCreate): Promise<ViewSummary> {
  return request<ViewSummary>(workspacePath(slug, "/views"), {
    method: "POST",
    body: JSON.stringify({ title: body.title, icon: body.icon, layout: body.layout }),
  });
}

/**
 * The sidebar's ranked entries — every view tab in one list, pinned or not.
 *
 * The built-in pages are *not* here: the Tools section is derived from the
 * client's own page catalog, so no stored row can leave the app without
 * navigation. Entries of kind `page` are leftovers from before that, and the
 * sidebar draws none of them.
 */
export function fetchSidebarEntries(slug: string): Promise<SidebarEntry[]> {
  return request<SidebarEntry[]>(workspacePath(slug, "/sidebar-entries"));
}

/**
 * Move a view's tab between the Pinned section and Tabs.
 *
 * The rank is untouched — the two sections share one ordering, and this says
 * which of them draws the tab, not where it sits. Refused with a 400 for
 * anything but a view entry.
 */
export function setEntryPinned(
  slug: string,
  entryId: string,
  pinned: boolean,
): Promise<SidebarEntry> {
  return request<SidebarEntry>(
    workspacePath(slug, `/sidebar-entries/${encodeURIComponent(entryId)}`),
    { method: "PATCH", body: JSON.stringify({ pinned }) },
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

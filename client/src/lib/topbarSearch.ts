import { SIDEBAR_PAGES, type SidebarPageDef } from "../components/appSidebarPages";

/** Pages whose name contains the query, in rail order. */
export function matchPages(query: string): SidebarPageDef[] {
  return matchText(SIDEBAR_PAGES, query, (page) => [page.label]);
}

/**
 * The items any of whose fields contains the query, case-insensitively, in
 * their own order, at most `limit` of them. The client-side half of search: a
 * list the app already holds (initiatives, chat threads, branches, tabs) is
 * filtered here rather than asked of the server again.
 */
export function matchText<T>(
  items: readonly T[],
  query: string,
  fields: (item: T) => readonly string[],
  limit = Infinity,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const found: T[] = [];
  for (const item of items) {
    if (fields(item).some((field) => field.toLowerCase().includes(needle))) {
      found.push(item);
      if (found.length >= limit) break;
    }
  }
  return found;
}

/** The sources search reads, in the order their groups are drawn. */
export type SearchGroup = "Pages" | "Tabs" | "Tickets" | "Initiatives" | "Chats" | "Branches";

/** A source that could not be asked, or has not answered yet. */
export interface SearchSourceState {
  group: SearchGroup;
  pending: boolean;
  /** Why it failed, already in words; null when it did not. */
  error: string | null;
}

/**
 * What the results say under the list: which sources are still answering, and
 * which failed and why. One line each, so a slow branch scan does not hide a
 * failed ticket search, and "nothing matches" is never said while a source
 * that might match has not answered.
 */
export function searchNotes(states: readonly SearchSourceState[]): {
  pending: string | null;
  errors: string[];
} {
  const waiting = states.filter((state) => state.pending).map((state) => state.group.toLowerCase());
  return {
    pending: waiting.length ? `Searching ${waiting.join(", ")}…` : null,
    errors: states
      .filter((state) => !state.pending && state.error !== null)
      .map((state) => `Couldn't search ${state.group.toLowerCase()}: ${state.error}`),
  };
}

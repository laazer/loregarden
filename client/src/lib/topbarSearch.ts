import type { ReactNode } from "react";

import { SIDEBAR_PAGES } from "../components/appSidebarPages";
import { pathForPage } from "./appNavigation";

/** A place search can open: a rail page, or a tab that used to be one. */
export interface SearchPage {
  key: string;
  label: string;
  path: string;
  icon: ReactNode;
  /** What kind of place it is, beside its name in the results. */
  meta: string;
  /** Other names it answers to, such as the page it replaced. */
  aliases: readonly string[];
}

/**
 * The rail's pages, plus Machine's Lanes tab right after Machine. Lanes was the
 * Queue page; it left the rail but not search, and still answers to "queue".
 */
const SEARCH_PAGES: readonly SearchPage[] = SIDEBAR_PAGES.flatMap((page): SearchPage[] => {
  const own: SearchPage = { key: page.key, label: page.label, path: page.path, icon: page.icon, meta: "Page", aliases: [] };
  if (page.key !== "machine") return [own];
  return [
    own,
    { key: "queue", label: "Lanes", path: pathForPage("queue"), icon: page.icon, meta: "Machine tab", aliases: ["Queue"] },
  ];
});

/** Places whose name (or former name) contains the query, in rail order. */
export function matchPages(query: string): SearchPage[] {
  return matchText(SEARCH_PAGES, query, (page) => [page.label, ...page.aliases]);
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

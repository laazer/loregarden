import { SIDEBAR_PAGES, type SidebarPageDef } from "../components/appSidebarPages";

/** Pages whose name contains the query, in rail order. */
export function matchPages(query: string): SidebarPageDef[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return SIDEBAR_PAGES.filter((page) => page.label.toLowerCase().includes(needle));
}

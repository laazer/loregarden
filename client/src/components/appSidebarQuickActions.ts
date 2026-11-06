/**
 * Quick actions: what right-clicking a sidebar page offers.
 *
 * Each action is a link into a flow the page already has, so the page stays
 * the one owner of its form (its fields, its validation, where the result
 * lands). The menu only gets the operator there in one step instead of two.
 * A page with no entry here keeps the browser's own context menu.
 */

import { memoryAddReferencePath, memoryAutoRereviewPath, type AppPage } from "../lib/appNavigation";

export interface SidebarQuickAction {
  /** Stable, for keys and tests: `<page>.<action>`. */
  id: string;
  label: string;
  /** Where choosing it goes: a route, optionally with the search that opens a flow. */
  to: string;
}

export const SIDEBAR_QUICK_ACTIONS: Partial<Record<AppPage, readonly SidebarQuickAction[]>> = {
  memory: [
    { id: "memory.add-reference", label: "Add Reference", to: memoryAddReferencePath() },
    { id: "memory.auto-rereview", label: "Auto Re-review…", to: memoryAutoRereviewPath() },
  ],
};

export function quickActionsFor(page: AppPage): readonly SidebarQuickAction[] {
  return SIDEBAR_QUICK_ACTIONS[page] ?? [];
}

/**
 * Quick actions: what right-clicking a sidebar page offers.
 *
 * An action either links into a flow the page owns (`to`), or opens a dialog
 * in place (`dialog`) so a quick add does not take the operator off the page
 * they were on. Either way the form itself is the page's own component, so its
 * fields and validation live in one place. A page with no entry here keeps the
 * browser's own context menu.
 */

import { memoryAutoRereviewPath, type AppPage } from "../lib/appNavigation";

/** Dialogs a quick action opens over the current page, rendered by the sidebar. */
export type SidebarQuickDialog = "add-reference";

interface QuickActionBase {
  /** Stable, for keys and tests: `<page>.<action>`. */
  id: string;
  label: string;
}

export type SidebarQuickAction =
  /** Goes to a route, optionally with the search that opens a flow there. */
  | (QuickActionBase & { to: string; dialog?: never })
  /** Opens a dialog over whatever page is showing. */
  | (QuickActionBase & { dialog: SidebarQuickDialog; to?: never });

export const SIDEBAR_QUICK_ACTIONS: Partial<Record<AppPage, readonly SidebarQuickAction[]>> = {
  memory: [
    { id: "memory.add-reference", label: "Add Reference…", dialog: "add-reference" },
    { id: "memory.auto-rereview", label: "Auto Re-review…", to: memoryAutoRereviewPath() },
  ],
};

export function quickActionsFor(page: AppPage): readonly SidebarQuickAction[] {
  return SIDEBAR_QUICK_ACTIONS[page] ?? [];
}

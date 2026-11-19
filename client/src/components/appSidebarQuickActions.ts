/**
 * Quick actions: what right-clicking a sidebar page offers.
 *
 * An action either links into a flow the page owns (`to`), or opens a dialog
 * in place (`dialog`) so a quick add does not take the operator off the page
 * they were on. Either way the form itself is the page's own component, so its
 * fields and validation live in one place. A page with no entry here keeps the
 * browser's own context menu.
 */

import {
  machinePath,
  memoryAutoRereviewPath,
  studioPath,
  studioTicketSessionNewPath,
  type AppPage,
} from "../lib/appNavigation";
import type { WorkItemsView } from "../lib/workItemsTable";

/** Dialogs a quick action opens over the current page, rendered by the sidebar. */
export type SidebarQuickDialog = "add-reference" | "usage" | "instances" | "new-agent" | "new-workflow";

/**
 * Dialogs that write against the active workspace, so cannot open on "all".
 * The rest (usage, instances, studio resources) are machine- or app-wide.
 */
const WORKSPACE_DIALOGS: ReadonlySet<SidebarQuickDialog> = new Set<SidebarQuickDialog>(["add-reference"]);

export function quickDialogNeedsWorkspace(dialog: SidebarQuickDialog): boolean {
  return WORKSPACE_DIALOGS.has(dialog);
}

function workItemsViewPath(view: WorkItemsView): string {
  return `/work-items?view=${view}`;
}

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
  "work-items": [
    { id: "work-items.needs-me", label: "Needs me", to: workItemsViewPath("needs_me") },
    { id: "work-items.running", label: "Running", to: workItemsViewPath("running") },
    { id: "work-items.blocked", label: "Blocked", to: workItemsViewPath("blocked") },
  ],
  studio: [
    { id: "studio.new-agent", label: "New Agent…", dialog: "new-agent" },
    { id: "studio.new-workflow", label: "New Workflow…", dialog: "new-workflow" },
    { id: "studio.new-ticket-session", label: "New Ticket Session", to: studioTicketSessionNewPath() },
    { id: "studio.gates", label: "Gate Studio", to: studioPath("gates") },
  ],
  machine: [
    { id: "machine.usage", label: "Usage…", dialog: "usage" },
    { id: "machine.instances", label: "Instances…", dialog: "instances" },
    { id: "machine.lanes", label: "Lanes", to: machinePath("lanes") },
    { id: "machine.disk", label: "Disk", to: machinePath("disk") },
    { id: "machine.tokens", label: "Tokens", to: machinePath("tokens") },
  ],
  memory: [
    { id: "memory.add-reference", label: "Add Reference…", dialog: "add-reference" },
    { id: "memory.auto-rereview", label: "Auto Re-review…", to: memoryAutoRereviewPath() },
  ],
};

export function quickActionsFor(page: AppPage): readonly SidebarQuickAction[] {
  return SIDEBAR_QUICK_ACTIONS[page] ?? [];
}

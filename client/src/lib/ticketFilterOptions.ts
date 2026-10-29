/** Labels for the ticket filter menus, shared by the Console and Work items. */

import type { BlockedOn, WorkItemType } from "../api/types";

export const TYPE_FILTERS: { id: WorkItemType; label: string }[] = [
  { id: "initiative", label: "Initiatives" },
  { id: "milestone", label: "Milestones" },
  { id: "feature", label: "Features" },
  { id: "capability", label: "Capabilities" },
  { id: "task", label: "Tasks" },
  { id: "bug", label: "Bugs" },
];

/** A person first: that is the question the filter exists to answer. */
export const BLOCKED_ON_FILTERS: { id: BlockedOn; label: string }[] = [
  { id: "person", label: "You" },
  { id: "tickets", label: "Other tickets" },
  { id: "agent", label: "An agent" },
];

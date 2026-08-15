/**
 * Panels that were considered for the container registry and ruled out, with
 * the reason each one is not embeddable as it stands.
 *
 * Recorded rather than dropped: without this list the next person to look at
 * the registry re-derives the same eight conclusions, and "it is missing"
 * reads as an oversight instead of a decision. Each entry names what would have
 * to change for the panel to become a primitive.
 */

export interface ExcludedPanel {
  /** Component name, as exported by its own module. */
  component: string;
  /** Why it is not in the registry, and what unblocks it. */
  reason: string;
}

export const EXCLUDED_PANELS: ExcludedPanel[] = [
  {
    component: "LogsPanel",
    reason:
      "Takes a whole TicketDetail plus its run list; a container's JSON settings cannot produce that object, only an id. Needs a ticket-id-driven wrapper first.",
  },
  {
    component: "ApprovalInboxPanel",
    reason:
      "Reads the singleton uiStore and renders into a fixed drawer, so two instances would fight over one open/closed flag and one drawer position.",
  },
  {
    component: "CopilotDock",
    reason:
      "Bound to the current route's chat session and to five uiStore singletons; embedding it in a container would move the session out from under the page that owns it.",
  },
  {
    component: "HiveSimulationPanel",
    reason:
      "Needs a TicketDetail, and shares a global skin and speed setting, so two panes could not disagree about either.",
  },
  {
    component: "FailedRunsPanel",
    reason: "No endpoint backs it; there is nothing for a self-fetching primitive to fetch.",
  },
  {
    component: "TicketDiffReviewPanel",
    reason:
      "Driven by the page's run list rather than by an id, so a container has nothing to pass it.",
  },
  {
    component: "InlineCodeDiffReview",
    reason:
      "Takes a DiffArtifact object held by its caller; settings can carry an id, not an artifact.",
  },
  {
    component: "QueueDashboard",
    reason:
      "Pins a 326px rail, which is fine on a page and overflows the small container size AC8 requires.",
  },
];

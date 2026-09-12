/**
 * Panels that were considered for the container registry and ruled out, with
 * the reason each one is not embeddable as it stands.
 *
 * Recorded rather than dropped: without this list the next person to look at
 * the registry re-derives the same conclusions, and "it is missing" reads as an
 * oversight instead of a decision. Each entry names what would have to change
 * for the panel to become a primitive.
 *
 * ## Two entries left, and what that cost
 *
 * `LogsPanel` and `ApprovalInboxPanel` were here, and both reasons were true as
 * written — and both named the work rather than a wall. The logs panel wanted "a
 * ticket-id-driven wrapper", which is `ticketLogsPrimitive`: one `api.ticket`
 * call. The inbox "reads the singleton uiStore and renders into a fixed drawer",
 * which was true of the *drawer* and not of the approvals inside it; splitting
 * `inbox/ApprovalsList` out left the drawer with what is genuinely singular.
 *
 * The lesson for the entries below: a reason that names a missing seam is a
 * to-do, not a verdict. Re-read them before assuming they still hold.
 */

export interface ExcludedPanel {
  /** Component name, as exported by its own module. */
  component: string;
  /** Why it is not in the registry, and what unblocks it. */
  reason: string;
}

export const EXCLUDED_PANELS: ExcludedPanel[] = [
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

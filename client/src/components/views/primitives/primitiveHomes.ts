/**
 * Where each primitive's data already lives in the app's own pages.
 *
 * Every container primitive is a second view of something the app was already
 * showing somewhere: the Queue Lane pane draws what the Parallel Execution page
 * draws, the Kanban pane what the Dashboard board draws. Until now the only way
 * to get one into a view was to open a tab, add a pane, and type the identifier
 * in — from the page that was already displaying exactly that thing.
 *
 * This is the map that closes the loop. `AddToTabMenu` reads it to decide which
 * surfaces offer "add to a tab", and each surface reads it for the primitive it
 * should offer.
 *
 * ## `null` is an answer, not a gap
 *
 * A primitive with no home is recorded as one. `web_embed` points at an
 * arbitrary URL and no page in this app is "the page about that URL"; leaving it
 * out of the map would be indistinguishable from forgetting it.
 *
 * ## The list is closed on purpose
 *
 * `PRIMITIVE_HOME_IDS` is the vocabulary, and `primitiveHomes.test` asserts it
 * matches the registry exactly in both directions. A primitive added without an
 * entry fails that test rather than silently having no way in; an entry left
 * behind by a deleted primitive fails it too.
 */

/** A page in the app, and the part of it a primitive corresponds to. */
export interface PrimitiveHome {
  /** The route the surface is on, for a menu that wants to say where it goes. */
  path: string;
  /** What that surface is called, in the words the app already uses for it. */
  surface: string;
}

export const PRIMITIVE_HOME_IDS = [
  "terminal",
  "run_ledger",
  "queue_lane",
  "chat_ticket",
  "chat_ticket_workflow",
  "chat_parent_ticket",
  "chat_ticket_list",
  "chat_gate",
  "chat_status_column",
  "chat_kanban",
  "chat_filterable_kanban",
  "chat_agent",
  "chat_workflow",
  "chat_workspace",
  "chat_branch_history",
  "chat_commit",
  "web_embed",
  "chat_session",
] as const;

export type HomedPrimitiveId = (typeof PRIMITIVE_HOME_IDS)[number];

export const PRIMITIVE_HOMES: Record<HomedPrimitiveId, PrimitiveHome | null> = {
  terminal: { path: "/console", surface: "Console" },
  run_ledger: { path: "/", surface: "Ticket details" },
  queue_lane: { path: "/queue", surface: "Parallel Execution" },

  chat_ticket: { path: "/", surface: "Ticket details" },
  chat_ticket_workflow: { path: "/", surface: "Ticket details" },
  chat_parent_ticket: { path: "/", surface: "Ticket details" },
  chat_ticket_list: { path: "/", surface: "Work items" },
  chat_gate: { path: "/", surface: "Ticket details" },

  chat_status_column: { path: "/", surface: "Board status" },
  chat_kanban: { path: "/", surface: "Board status" },
  chat_filterable_kanban: { path: "/", surface: "Board status" },

  chat_agent: { path: "/studio/agents", surface: "Agent Studio" },
  chat_workflow: { path: "/studio/agents", surface: "Workflow Studio" },

  chat_workspace: { path: "/", surface: "Workspaces" },

  chat_branch_history: { path: "/branch-triage", surface: "Branch Triage" },
  chat_commit: { path: "/branch-triage", surface: "Branch Triage" },

  // The conversation the chat page is showing. Home holds one too, but the
  // chat page is where a thread is chosen, which is what the pane needs.
  chat_session: { path: "/chat", surface: "Chat" },

  // Points at an arbitrary URL. No page in this app is the page about that URL,
  // so there is nowhere to offer it from — which is a decision, recorded.
  web_embed: null,
};

/** The home of `primitiveId`, or `undefined` when the map does not name it. */
export function homeOf(primitiveId: string): PrimitiveHome | null | undefined {
  return Object.hasOwn(PRIMITIVE_HOMES, primitiveId)
    ? PRIMITIVE_HOMES[primitiveId as HomedPrimitiveId]
    : undefined;
}

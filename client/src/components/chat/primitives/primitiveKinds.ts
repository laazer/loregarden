/**
 * Each card kind's icon and the tint of the circle it sits in.
 *
 * The tint says what kind of object a card is, not its state (that is the
 * status chip): sage for work, blue for a plan an agent is running, lavender
 * for something waiting on the operator, grey for ambient cards that only
 * show something. A card can name a different tint for one render, such as a
 * question card once it has been answered.
 */

export type PrimitiveTint = "work" | "run" | "you" | "mute";

const ICON_PATHS: Record<string, string> = {
  ticket: "M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z",
  ticket_list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  kanban: "M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v13h-4z",
  agent: "M5 9h14v10H5zM12 5v4M9.5 13.5h.01M14.5 13.5h.01M9 17h6",
  workflow: "M4 5h5v5H4zM15 14h5v5h-5zM9 7.5h3a2 2 0 0 1 2 2v5a2 2 0 0 0 1 1.7",
  gate: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6zM9 12l2 2 4-4",
  terminal: "M5 7l5 5-5 5M12 17h7",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  workspace: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  plan: "M4 6.5l2 2 3-3M4 14.5l2 2 3-3M12 7h8M12 15h8",
  branch: "M6 3v12M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  commit: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 12h6M15 12h6",
  question: "M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5h.01",
  aside: "M4 5h16v11H9l-5 4z",
  gif: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  card: "M5 5h14v14H5z",
};

/** A primitive kind's icon and default tint. */
const KINDS: Record<string, { icon: keyof typeof ICON_PATHS; tint: PrimitiveTint }> = {
  ticket: { icon: "ticket", tint: "work" },
  ticket_workflow: { icon: "ticket", tint: "work" },
  parent_ticket: { icon: "ticket", tint: "work" },
  ticket_list: { icon: "ticket_list", tint: "work" },
  status_column: { icon: "kanban", tint: "work" },
  kanban: { icon: "kanban", tint: "work" },
  filterable_kanban: { icon: "kanban", tint: "work" },
  agent: { icon: "agent", tint: "work" },
  workflow: { icon: "workflow", tint: "work" },
  gate: { icon: "gate", tint: "work" },
  terminal: { icon: "terminal", tint: "work" },
  edit: { icon: "edit", tint: "work" },
  calendar: { icon: "calendar", tint: "mute" },
  calendar_event: { icon: "calendar", tint: "mute" },
  workspace: { icon: "workspace", tint: "work" },
  todo_list: { icon: "plan", tint: "run" },
  branch_history: { icon: "branch", tint: "work" },
  commit: { icon: "commit", tint: "work" },
  qa: { icon: "question", tint: "you" },
  btw: { icon: "aside", tint: "mute" },
  giphy: { icon: "gif", tint: "mute" },
};

export function primitiveTint(kind: string): PrimitiveTint {
  return KINDS[kind]?.tint ?? "mute";
}

/** The kind's glyph, as one path in a 24-unit box. */
export function primitiveIconPath(kind: string): string {
  return ICON_PATHS[KINDS[kind]?.icon ?? "card"];
}

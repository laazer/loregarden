/**
 * The verdict on every chat primitive, as a view container (557).
 *
 * ## Why this is a `Record<PrimitiveKind, …>` and not an array
 *
 * AC1 asks that *every one of the 23* has a recorded verdict. An array of 23
 * objects satisfies that on the day it is written and silently stops the day a
 * 24th primitive is added to `chat/primitives/types.ts` — the same way 436's
 * panel list would have. Keyed by `PrimitiveKind`, a new chat primitive fails
 * the build here until someone decides what it is, so the record cannot go
 * stale without anyone noticing.
 *
 * ## The three verdicts
 *
 * A chat primitive renders a `part` an agent wrote inside a turn. A view
 * container renders from `settings` and fetches its own data. So the question
 * asked of each one is exactly: **can the part it needs be rebuilt from a few
 * scalar settings an operator can type, after which the component fetches
 * everything else itself?**
 *
 *   - `adaptable` — the part is scalars the operator already knows (a ticket
 *     id, a branch, a slug), and the component fetches from them.
 *   - `adaptable-with-wrapper` — the same, but one field needs a shape `SettingsField` cannot
 *     express. Every case here is a list, and the wrapper is a comma-separated
 *     string split in `parseSettings`. That conversion is the *only* thing the
 *     wrapper does; it lives in `chatPanePrimitives`, and it changes no chat
 *     code.
 *   - `chat-bound` — the part carries content rather than an identifier, or its
 *     identifier is only knowable from inside a thread, or the component reads
 *     page-level state. Recorded and left out of the registry, per AC3 and the
 *     `EXCLUDED_PANELS` precedent 436 set.
 *
 * A primitive needing a change to a chat component or to the chat vocabulary is
 * out of scope by the ticket's own rule, and is recorded `chat-bound` with that
 * as its reason rather than half-wired in.
 */

import type { PrimitiveKind } from "../../chat/primitives/types";

export type ChatPrimitiveVerdict = "adaptable" | "adaptable-with-wrapper" | "chat-bound";

export interface ChatPrimitiveAudit {
  verdict: ChatPrimitiveVerdict;
  /** What it needs to render — the evidence the verdict is drawn from. */
  needs: string;
  /** Why that does, or does not, reduce to settings plus a fetch. */
  reason: string;
}

export const CHAT_PRIMITIVE_VERDICTS: Record<PrimitiveKind, ChatPrimitiveAudit> = {
  // ── Adaptable: an id in, a fetch out ──────────────────────────────────────
  ticket: {
    verdict: "adaptable",
    needs: "`ticket_id`; the card fetches `api.ticket` and polls it.",
    reason: "One string setting, and every other value on the card comes from the fetch.",
  },
  ticket_workflow: {
    verdict: "adaptable",
    needs: "`ticket_id`; same fetch as `ticket`, plus the stage timeline off `data.stages`.",
    reason: "The timeline is drawn from the fetched ticket, so the setting is still one id.",
  },
  parent_ticket: {
    verdict: "adaptable",
    needs: "`ticket_id`; fetches the ticket and `api.tickets({parent_ticket_id})`.",
    reason:
      "Both fetches key off the one id. The expanded-row state is `useState`, so two panes " +
      "keep their own.",
  },
  agent: {
    verdict: "adaptable",
    needs: "`slug`; fetches `api.studioAgent` then previews it.",
    reason:
      "The `draft` branch renders a part-carried object, but it is optional — a pane simply " +
      "never sets it, and the slug branch is the whole of what a pane needs.",
  },
  workflow: {
    verdict: "adaptable",
    needs: "`workflow_slug`; fetches `api.studioWorkflow` and lays its stages out.",
    reason:
      "Slug in, graph out. `draft` is optional and unset for a pane. The one entry whose " +
      "chat stylesheet asserts a height — its ReactFlow canvas is 280px there — so " +
      "`paneChrome.css` shortens it for a pane. It has to stay a *definite* height: " +
      "ReactFlow's root is `height: 100%` and resolves to zero against an auto-height " +
      "ancestor, which drew an empty box with the stages translated outside it.",
  },
  gate: {
    verdict: "adaptable",
    needs: "`ticket_id` and `stage_key`; fetches the ticket and its approvals.",
    reason:
      "Two string settings. The approve/advance mutations act on the fetched ticket rather " +
      "than navigating, so they stay useful inside a pane.",
  },
  workspace: {
    verdict: "adaptable",
    needs: "`workspace_slug`; fetches `api.workspaces()` and picks its row out.",
    reason: "One slug, one fetch, no part-carried content.",
  },
  branch_history: {
    verdict: "adaptable",
    needs: "`workspace_slug`, `branch`, and a `limit`; fetches the branch's activity.",
    reason: "Two strings and a count — the count is the one numeric setting in the set.",
  },
  commit: {
    verdict: "adaptable",
    needs: "`workspace_slug` and `sha`, with an optional `branch` for its action.",
    reason: "Three strings, one fetch.",
  },

  // ── Wrapper: one list field, expressed as a comma-separated string ────────
  ticket_list: {
    verdict: "adaptable-with-wrapper",
    needs: "`parent_ticket_id` (fetches that parent's children) or an explicit `ticket_ids`.",
    reason:
      "`parent_ticket_id` alone is a plain string setting. `ticket_ids` is a list, which " +
      "`SettingsField` has no kind for, so the pane takes a comma-separated string and " +
      "splits it. Nothing in the chat component changes.",
  },
  status_column: {
    verdict: "adaptable-with-wrapper",
    needs: "a `status` to filter on, and optionally the `ticket_ids` to draw from.",
    reason: "`status` is a string; `ticket_ids` is the same comma-list wrapper as above.",
  },
  kanban: {
    verdict: "adaptable-with-wrapper",
    needs: "`statuses` (the columns) and optionally `ticket_ids`.",
    reason:
      "Both are lists, both are comma-list settings. Left empty, `statuses` falls back to " +
      "the component's own five and `ticket_ids` to the unfiltered ticket list — which the " +
      "chat component's own comment calls 'megabytes and seconds'. That is a cost a pane " +
      "pays on a 30s poll where a thread paid it once, and it is why the `ticket_ids` field " +
      "is offered rather than left implicit. Not a reason to exclude it: a board of every " +
      "ticket is the thing an operator wants a board for, react-query dedupes the request " +
      "across panes, and narrowing it is one field away.",
  },
  filterable_kanban: {
    verdict: "adaptable-with-wrapper",
    needs: "the same as `kanban`, plus `filters` — the subset offered as toggles.",
    reason:
      "A third comma-list. The toggle state is `useState` seeded from the part, so two " +
      "boards in one view filter independently.",
  },

  // ── Chat-bound: content, not an identifier ───────────────────────────────
  text: {
    verdict: "chat-bound",
    needs: "`content` — the turn's prose, rendered as markdown.",
    reason:
      "The part is the content. A pane could hold a string an operator typed, but that is a " +
      "notes primitive, not this one, and it fetches nothing.",
  },
  thinking: {
    verdict: "chat-bound",
    needs: "`content` — one turn's reasoning trace.",
    reason:
      "Reasoning belongs to the turn that produced it. There is no id by which a pane could " +
      "ask for 'the reasoning', and nothing to fetch.",
  },
  edit: {
    verdict: "chat-bound",
    needs: "`content` and `original` — the two sides of a diff an agent proposed.",
    reason:
      "Both sides are carried in the part. A pane's settings can hold a path but not a file's " +
      "before-and-after text, and no endpoint turns the path into the proposed edit.",
  },
  terminal: {
    verdict: "chat-bound",
    needs: "`lines[]` — a transcript the agent already captured.",
    reason:
      "It replays a recorded transcript, not a session. A pane wanting a shell already has " +
      "436's `terminal` primitive, which attaches to a live one.",
  },
  todo_list: {
    verdict: "chat-bound",
    needs: "`items[]`, and an `onSubmit` that posts the plan back into the thread.",
    reason:
      "The items are the part, and the Run button's whole effect is to send a chat message. " +
      "A pane has no thread to send it to.",
  },
  qa: {
    verdict: "chat-bound",
    needs: "`items[]` of questions, and an `onSubmit` to send the answers.",
    reason: "Same as `todo_list`: content in the part, and its action posts to a thread.",
  },
  btw: {
    verdict: "chat-bound",
    needs: "`ticket_id` and `exchange_id`; it does fetch the aside from those.",
    reason:
      "The honest near-miss. It reduces to two ids and a fetch, but an `exchange_id` is " +
      "minted inside a chat exchange and surfaced nowhere else in the app, so the settings " +
      "field would be one no operator could fill. Its escalation also writes into a running " +
      "agent's input — an action that belongs where the conversation is.",
  },
  giphy: {
    verdict: "chat-bound",
    needs: "`giphy_id` or a Giphy media `url`, and an alt/caption.",
    reason:
      "Reducible to a setting, but it fetches nothing and shows nothing about the workspace: " +
      "as a pane it is an image the operator pasted. An aside in a thread, per the ticket.",
  },
  calendar: {
    verdict: "chat-bound",
    needs: "a date range, and a workspace to fetch events for.",
    reason:
      "It reads the workspace from the `uiStore` singleton with no part-level override, so " +
      "two panes could not disagree about it and AC8 forbids the import outright. Fixing it " +
      "means a `workspace_slug` on `CalendarPart`, which is the chat vocabulary the server " +
      "mirrors — surgery, and out of scope by the ticket's own rule.",
  },
  calendar_event: {
    verdict: "chat-bound",
    needs: "a whole `CalendarEventItem` — title, start, end, kind, description.",
    reason:
      "A caller-held object, not an id: it fetches nothing, so a pane would show an event " +
      "the operator retyped into settings rather than one the calendar holds. Same reason " +
      "436 excluded `InlineCodeDiffReview`.",
  },
};

/** The ids this audit says belong in the container registry. */
export function adaptablePrimitiveKinds(): PrimitiveKind[] {
  return (Object.keys(CHAT_PRIMITIVE_VERDICTS) as PrimitiveKind[]).filter(
    (kind) => CHAT_PRIMITIVE_VERDICTS[kind].verdict !== "chat-bound",
  );
}

/** The ids it says do not, each with the reason AC3 asks be recorded. */
export function chatBoundPrimitiveKinds(): PrimitiveKind[] {
  return (Object.keys(CHAT_PRIMITIVE_VERDICTS) as PrimitiveKind[]).filter(
    (kind) => CHAT_PRIMITIVE_VERDICTS[kind].verdict === "chat-bound",
  );
}

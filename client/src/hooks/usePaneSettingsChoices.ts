/**
 * What a pane settings field offers instead of an empty text box.
 *
 * Every field a primitive declared was `kind: "string"`, and most of them name
 * something from a set the app already knows: a workspace slug, a ticket id, an
 * agent, a workflow, a ticket state. An operator opening the settings for a
 * Terminal pane was asked to type a slug with nothing on screen to say what the
 * slugs are — so the form was, for twelve of the sixteen primitives, a quiz.
 *
 * This resolves a `ChoiceSource` to the options for that set, and says how they
 * should be offered:
 *
 * - **`select`** for a set an operator picks from — workspaces, agents,
 *   workflows, ticket states. Short, closed, and worth showing whole.
 * - **`suggest`** for tickets, which number in the hundreds here. A select of
 *   532 options is not a picker, it is a wall; a text input with a `datalist`
 *   filters as you type and still accepts an id pasted from elsewhere.
 *
 * ## Nothing here can block an edit
 *
 * `PaneSettingsEditor` deliberately does not validate that a slug exists — a
 * value naming something absent renders the primitive's own empty state, which
 * is the honest outcome. Options are a convenience laid over that rule, never a
 * constraint on it: a failed or empty fetch degrades to the plain text box the
 * field had before, and a stored value the list does not contain is kept and
 * shown. The editor is where that happens; this hook only reports which case it
 * is in.
 *
 * ## Why every source queries unconditionally
 *
 * Hooks cannot be called on a branch, so all five queries exist on every render
 * and `enabled` decides which one actually fetches. A disabled query costs
 * nothing beyond its own hook call, and the alternative — a hook per source at
 * each call site — is a switch the editor would have to write instead.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { baxterChatSessionsKey } from "./useBaxterChatSession";
import { TICKET_STATE_LABELS } from "../lib/ticketStates";
import { useQueueLanes } from "./useQueueLanes";
import type { ChoiceSource } from "../components/views/primitives/types";

export interface ChoiceOption {
  /** Stored verbatim as the field's value. */
  value: string;
  label: string;
}

/** How the editor should offer the options: a closed list, or suggestions. */
export type ChoiceMode = "select" | "suggest";

export interface ChoiceList {
  options: ChoiceOption[];
  mode: ChoiceMode;
  /** True while the source is still being fetched — the input waits, not fails. */
  isLoading: boolean;
  /**
   * True when the options cannot be offered at all — the fetch failed, or it
   * succeeded and returned nothing. Both mean the same thing to the operator,
   * and the editor's answer to both is the text box.
   */
  isUnavailable: boolean;
}

const TICKET_STATE_OPTIONS: ChoiceOption[] = Object.entries(TICKET_STATE_LABELS).map(
  ([value, label]) => ({ value, label }),
);

/**
 * What a ticket suggestion stores, and what it reads as.
 *
 * The value is the **external id**, not the row id. Both resolve — the ticket
 * endpoint accepts either, checked against the running server — and a datalist
 * puts its `value` in the box, where `lg-flex-views-557` is something an
 * operator can recognise and a UUID is something they can only trust. The row
 * id is the fallback for a ticket that has no external id rather than the
 * default.
 */
function ticketChoice(ticket: { external_id: string; id: string; title: string }): ChoiceOption {
  const value = ticket.external_id === "" ? ticket.id : ticket.external_id;
  return { value, label: ticket.title === "" ? value : `${value} · ${ticket.title}` };
}

export function useChoiceOptions(source: ChoiceSource, workspaceSlug: string): ChoiceList {
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    enabled: source === "workspace",
  });
  const agents = useQuery({
    queryKey: ["studio-agents"],
    queryFn: api.studioAgents,
    enabled: source === "agent",
  });
  const workflows = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: api.workflowTemplates,
    enabled: source === "workflow",
  });
  // The same shared key the lane panes and the run dialogs' picker read, so
  // opening this form costs nothing when a lane is already on screen.
  const lanesQuery = useQueueLanes(source === "lane");

  const sessions = useQuery({
    // The same key the chat page's archive reads, so choosing a thread here
    // costs nothing when the archive is already loaded.
    queryKey: baxterChatSessionsKey(workspaceSlug),
    queryFn: () => api.baxterChatSessions(workspaceSlug),
    enabled: source === "chat_session" && workspaceSlug !== "",
  });

  const tickets = useQuery({
    // Scoped to the workspace the chrome is showing, which is the one the view
    // belongs to. An unscoped list would offer tickets from workspaces this
    // pane cannot render.
    queryKey: ["tickets", "pane-settings", workspaceSlug],
    queryFn: () => api.tickets({ workspace: workspaceSlug }),
    enabled: source === "ticket" && workspaceSlug !== "",
  });

  if (source === "ticket_state") {
    return { options: TICKET_STATE_OPTIONS, mode: "select", isLoading: false, isUnavailable: false };
  }

  const resolved: Record<Exclude<ChoiceSource, "ticket_state">, ChoiceList> = {
    workspace: {
      options: (workspaces.data ?? []).map((workspace) => ({
        value: workspace.slug,
        label: workspace.name === "" ? workspace.slug : workspace.name,
      })),
      mode: "select",
      isLoading: workspaces.isLoading,
      isUnavailable: workspaces.isError || (workspaces.data ?? []).length === 0,
    },
    agent: {
      options: (agents.data ?? []).map((agent) => ({
        value: agent.slug,
        label: agent.name === "" ? agent.slug : agent.name,
      })),
      mode: "select",
      isLoading: agents.isLoading,
      isUnavailable: agents.isError || (agents.data ?? []).length === 0,
    },
    workflow: {
      options: (workflows.data ?? []).map((workflow) => ({
        value: workflow.slug,
        label: workflow.name === "" ? workflow.slug : workflow.name,
      })),
      mode: "select",
      isLoading: workflows.isLoading,
      isUnavailable: workflows.isError || (workflows.data ?? []).length === 0,
    },
    lane: {
      options: lanesQuery.lanes.map((lane) => ({
        value: String(lane.slot_number),
        label:
          lane.running === null
            ? `Lane ${lane.slot_number} · idle`
            : `Lane ${lane.slot_number} · running`,
      })),
      mode: "select",
      isLoading: lanesQuery.isLoading,
      isUnavailable: lanesQuery.isError || lanesQuery.lanes.length === 0,
    },
    chat_session: {
      options: (sessions.data ?? []).map((session) => ({
        value: session.id,
        // A thread with no title yet is named by when it started, because "" in
        // a dropdown of five conversations is indistinguishable from the rest.
        label: session.title === "" ? `Untitled · ${session.updated_at}` : session.title,
      })),
      mode: "select",
      isLoading: workspaceSlug !== "" && sessions.isLoading,
      isUnavailable:
        workspaceSlug === "" || sessions.isError || (sessions.data ?? []).length === 0,
    },
    ticket: {
      options: (tickets.data ?? []).map(ticketChoice),
      mode: "suggest",
      // With no workspace resolved the query never runs, so react-query reports
      // it as loading forever. That is a missing prerequisite, not a fetch in
      // flight, and the field should offer its text box rather than a spinner
      // that never resolves.
      isLoading: workspaceSlug !== "" && tickets.isLoading,
      isUnavailable:
        workspaceSlug === "" || tickets.isError || (tickets.data ?? []).length === 0,
    },
  };

  return resolved[source];
}

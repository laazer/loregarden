/**
 * The UI actions an agent may ask this tab to perform, and their arguments.
 *
 * Mirrors `server/loregarden/services/ui_action_catalog.py`, which owns the
 * list and each action's effect (view / write / human-only). The server
 * decides whether an agent may invoke an action before this tab is ever asked,
 * so nothing here grants permission — a tab only says which actions it can
 * perform right now. `server/tests/test_ui_actions.py` fails when the names
 * here and there drift.
 *
 * To make a control drivable, register a handler where the control lives:
 *
 *     useAgentAction("ticket.set_state", async ({ ticket_id, state }) => { … });
 *
 * The action is offered while the component is mounted, and withdrawn when it
 * unmounts.
 */

import type { AppPage } from "../appNavigation";
import type { TicketState } from "../../api/types";

export interface UiActionArgs {
  "navigate.page": { page: AppPage };
  "ticket.open": { ticket_id: string };
  "ticket.update": {
    ticket_id: string;
    title?: string;
    description?: string;
    acceptance_criteria?: string[];
    priority?: number;
  };
  "ticket.set_state": { ticket_id: string; state: TicketState };
  /** Human-only on the server: never invoked by an agent, so nothing registers it. */
  "approval.resolve": { approval_id: string };
}

export type UiActionName = keyof UiActionArgs;

/** What a handler returns is relayed to the agent as the action's result. */
export type UiActionHandler<N extends UiActionName> = (args: UiActionArgs[N]) => Promise<unknown>;

/** Plain-language names for the toast the operator sees when an agent acts. */
export const UI_ACTION_LABELS: Record<UiActionName, string> = {
  "navigate.page": "Opened a page",
  "ticket.open": "Opened a ticket",
  "ticket.update": "Edited the ticket",
  "ticket.set_state": "Changed the ticket's state",
  "approval.resolve": "Resolved an approval",
};

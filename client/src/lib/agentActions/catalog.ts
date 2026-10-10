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
import type { TicketState, WorkspaceRuntimeSettings } from "../../api/types";

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
  "ticket.trigger_auto_fix": { ticket_id: string };
  "ticket.merge_pull_request": { ticket_id: string; number: number; head_sha: string };
  "workspace.archive": { workspace_slug: string };
  "workspace.restore": { workspace_slug: string };
  "workspace.set_workflow": { workspace_slug: string; template: string };
  "reference_repo.add": { workspace_slug: string; url: string; notes?: string };
  "reference_repo.sync": { reference_repo_id: string };
  "ticket.start_stage": { ticket_id: string; stage_key: string };
  "ticket.stop": { ticket_id: string };
  "ticket.set_runtime": { ticket_id: string } & Partial<WorkspaceRuntimeSettings>;
  "triage.set_runtime": { ticket_id: string } & Partial<WorkspaceRuntimeSettings>;
  "workspace.set_runtime": { workspace_slug: string } & Partial<WorkspaceRuntimeSettings>;
  "workspace.create": { slug: string; name: string; repo_path?: string; workflow_template_slug?: string };
  "workspace.create_repository": { workspace_slug: string };
  "run.send_message": { run_id: string; message: string };
  "run.cancel": { run_id: string };
  "queue.promote": { run_id: string };
  "queue.cancel": { run_id: string };
  "capacity.release": { lease_id: string; reason: string };
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
  "ticket.trigger_auto_fix": "Started the CI auto-fix",
  "ticket.merge_pull_request": "Merged the ticket's pull request",
  "workspace.archive": "Archived a workspace",
  "workspace.restore": "Restored a workspace",
  "workspace.set_workflow": "Changed a workspace's workflow",
  "reference_repo.add": "Added a reference repo",
  "reference_repo.sync": "Synced a reference repo",
  "ticket.start_stage": "Started a stage",
  "ticket.stop": "Stopped the ticket's work",
  "ticket.set_runtime": "Changed the ticket's runtime",
  "triage.set_runtime": "Changed the triage runtime",
  "workspace.set_runtime": "Changed a workspace's runtime",
  "workspace.create": "Added a workspace",
  "workspace.create_repository": "Created a workspace's repository",
  "run.send_message": "Sent a steering message",
  "run.cancel": "Stopped a run",
  "queue.promote": "Moved a run to the front of the queue",
  "queue.cancel": "Removed a run from the queue",
  "capacity.release": "Ended a machine-capacity lease",
  "approval.resolve": "Resolved an approval",
};

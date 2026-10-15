import { api, type TicketDetail, type WorkflowStageView } from "../api/client";
import { dismissToast, pushToast, toastActionFailed, toastWarning } from "../state/toastStore";
import { copyText } from "./clipboard";

export function isAgentWorkflowTicket(ticket: TicketDetail): boolean {
  return Boolean(ticket.workflow_template_slug?.trim());
}

const DEFAULT_API_BASE = "http://127.0.0.1:8000";

export function buildOrchestrateTerminalCommand(
  ticket: TicketDetail,
  apiBase: string = DEFAULT_API_BASE,
): string {
  const base = apiBase.replace(/\/$/, "");
  const title = ticket.title.replace(/"/g, '\\"');
  return [
    `# Loregarden: orchestrate ticket ${ticket.external_id} — ${title}`,
    `curl -sS -X POST '${base}/api/tickets/${ticket.id}/orchestrate' \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '{}'`,
  ].join("\n");
}

export function buildStageRunTerminalCommand(
  ticket: TicketDetail,
  stage: WorkflowStageView,
  apiBase: string = DEFAULT_API_BASE,
): string {
  const base = apiBase.replace(/\/$/, "");
  const title = ticket.title.replace(/"/g, '\\"');
  const body = JSON.stringify({ manual: true, stage_key: stage.key });
  return [
    `# Loregarden: run stage "${stage.name}" (${stage.key}) for ${ticket.external_id} — ${title}`,
    `curl -sS -X POST '${base}/api/tickets/${ticket.id}/start' \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '${body}'`,
  ].join("\n");
}

/**
 * Copy the command that runs this stage in the operator's own terminal.
 *
 * The copied text launches the configured coding agent (Claude Code or Cursor)
 * directly instead of hitting POST /start, so the run survives the app's dev
 * server restarting mid-run.
 *
 * The server call is not quick: it brings the ticket's worktree up to its
 * target branch first (measured live, 33 of 39 worktrees behind by a median of
 * 40 commits and a maximum of 134), so this owns the whole flow — a sticky
 * in-flight toast, the call, the clipboard write, then exactly one outcome
 * toast. `OverflowMenu` closes on select, so the toast is the only place left
 * to say what happened.
 *
 * Returns true when the command reached the clipboard. A degraded tree still
 * returns true: a handoff is not blocked by a tree that could not be refreshed,
 * it is reported.
 */
export async function copyStageTerminalHandoffCommand(
  ticket: TicketDetail,
  stage: WorkflowStageView,
): Promise<boolean> {
  const title = ticket.title.replace(/"/g, '\\"');
  // The target branch is resolved server-side and first arrives in the response
  // this toast is waiting on, so it is named by role rather than by name here.
  //
  // `ticket.branch` is empty on most tickets — measured on a prod-shape
  // snapshot, 46 of 400 carry one — and the branch it will actually be is
  // derived server-side from the milestone, which this surface cannot see. So
  // the clause names the tree generically rather than rendering a gap where a
  // branch name was promised.
  const movingTree = ticket.branch?.trim() || "its worktree";
  const preparing = pushToast({
    tone: "info",
    title: "Preparing terminal command",
    message:
      `Preparing terminal command for ${stage.name} — bringing ${movingTree} ` +
      `up to its target branch.`,
    duration: 0,
  });
  try {
    const handoff = await api.buildTerminalHandoffCommand(ticket.id, stage.key);
    await copyText(
      [
        `# Loregarden: run stage "${stage.name}" (${stage.key}) for ${ticket.external_id} — ${title} — via terminal ${handoff.adapter} agent`,
        handoff.command,
      ].join("\n"),
    );
    // Pushed only once the clipboard has accepted the command: a warning about
    // the tree, next to no command, names a problem the operator cannot act on.
    if (handoff.tree_warning) {
      toastWarning(
        `${stage.name} terminal command copied`,
        handoff.tree_warning,
        handoff.tree_warning,
      );
    } else {
      pushToast({
        tone: "success",
        title: `${stage.name} terminal command copied`,
        // The branch comes before the path deliberately: `.toast__message`
        // clamps at about four lines (69.6px, ~188 characters at 1600px,
        // measured) and an absolute worktree path is 70–130 of them, so a
        // path-first sentence clips the branch off the end — the one fact the
        // operator cannot read anywhere else on this surface.
        message:
          `Paste it to run ${stage.name} for ${ticket.external_id}, brought up to ` +
          `${handoff.target_branch}, in ${handoff.repo_path}.`,
      });
    }
    return true;
  } catch (error) {
    toastActionFailed("Copy terminal command", error);
    return false;
  } finally {
    // In a `finally`, not at the end of either branch: the request can succeed
    // and the clipboard still refuse, and a sticky toast left there stays on
    // screen forever.
    dismissToast(preparing);
  }
}

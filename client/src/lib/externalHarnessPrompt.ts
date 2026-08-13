import { api, type ExternalHarness, type TicketDetail } from "../api/client";
import { pushToast, toastActionFailed } from "../state/toastStore";
import { copyText } from "./clipboard";

/** Harnesses offered in the ticket menu, in the order they appear. */
export const EXTERNAL_HARNESSES: ExternalHarness[] = ["claude_code", "codex", "cursor", "other"];

export const EXTERNAL_HARNESS_LABELS: Record<ExternalHarness, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
  other: "Other harness",
};

/**
 * Copy the prompt that runs this ticket in a harness outside loregarden.
 *
 * The prompt carries the MCP calls that record stage progress and timing, and
 * stamps the run with `harness` so its results can be compared against a run of
 * the control plane's own agents. Nothing is started here — the pasted prompt
 * opens the run when the harness actually begins.
 */
export async function copyExternalHarnessPrompt(
  ticket: TicketDetail,
  harness: ExternalHarness,
): Promise<boolean> {
  const label = EXTERNAL_HARNESS_LABELS[harness];
  try {
    const { prompt } = await api.buildExternalHarnessPrompt(ticket.id, harness);
    await copyText(prompt);
    pushToast({
      tone: "success",
      title: `${label} prompt copied`,
      message: `Paste it into ${label} to run ${ticket.external_id}. It reports stage progress and timing back over MCP, outside the queue.`,
    });
    return true;
  } catch (error) {
    toastActionFailed(`Copy ${label} prompt`, error);
    return false;
  }
}

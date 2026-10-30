import type { ChatWait } from "../../lib/chatWait";
import { formatWaitRemaining, waitCommandOneLine } from "../../lib/chatWait";

/**
 * Trailing wait row on a chat timeline: command, log path, time until the 2h deadline.
 *
 * One status region after the last message — not a thinking walker, not a bar.
 */
export function ChatWaitStatus({ wait }: { wait: ChatWait }) {
  const command = waitCommandOneLine(wait.command);
  return (
    <div className="lg-chat-wait" role="status" aria-live="polite">
      <span className="lg-chat-wait-command" title={wait.command}>
        {command}
      </span>
      <span className="lg-chat-wait-log" style={{ fontFamily: "var(--mono)" }}>
        {wait.log_path}
      </span>
      <span className="lg-chat-wait-remaining">{formatWaitRemaining(wait.deadline)}</span>
    </div>
  );
}

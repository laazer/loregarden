import { useCallback, useState } from "react";

import { copyText } from "../../lib/clipboard";
import { describeError, pushToast } from "../../state/toastStore";
import { OverflowMenu, OverflowMenuItem } from "../OverflowMenu";
import { Button } from "../ui/Button";
import type { ChatMessageView } from "./chatUtils";

/**
 * What an operator can do with a reply once it has landed.
 *
 * Copy is always here because it needs nothing from the host. The other two
 * depend on the surface: only the Baxter thread keeps an archive it can branch,
 * and only a surface with a workspace can file a ticket. A handler the host
 * does not pass leaves its control out rather than showing a dead one — the
 * same rule the `/` menu follows for its builtins.
 *
 * The row is always in the DOM rather than mounted on hover: a control that
 * only exists while the pointer is over the turn is a control no keyboard can
 * reach. CSS fades it in for the mouse; Tab brings it back at full opacity.
 */
export interface ChatMessageActionHandlers {
  /** Branch the thread at this turn, dropping everything after it. */
  onFork?: (message: ChatMessageView) => void | Promise<unknown>;
  /** File this reply as a ticket and open it. */
  onStartTicket?: (message: ChatMessageView) => void | Promise<unknown>;
}

export function ChatMessageActions({
  message,
  body,
  handlers,
}: {
  message: ChatMessageView;
  /** The rendered text of the turn — what Copy puts on the clipboard. */
  body: string;
  handlers: ChatMessageActionHandlers;
}) {
  const [pending, setPending] = useState<"fork" | "ticket" | null>(null);
  const { onFork, onStartTicket } = handlers;

  const copy = useCallback(() => {
    // `copyText` already falls back to a hidden textarea when the async API is
    // unavailable; a rejection here means both routes failed, which the
    // operator has to hear about — a Copy that silently did nothing is worse
    // than one that says so.
    copyText(body).then(
      () => pushToast({ title: "Copied", message: "The message is on your clipboard.", tone: "success" }),
      (error: unknown) =>
        pushToast({
          title: "Copy",
          message: describeError(error, "Could not copy the message"),
          tone: "error",
        }),
    );
  }, [body]);

  // One in-flight action at a time, and the row says which: a second Fork click
  // before the first returns would mint two branches off the same turn.
  const run = useCallback(
    (kind: "fork" | "ticket", action: (message: ChatMessageView) => void | Promise<unknown>) => {
      if (pending) return;
      setPending(kind);
      Promise.resolve(action(message))
        .catch(() => {
          // silent-ok: every handler reports its own failure — fork toasts from
          // `useChatMessageActions`, and the ticket mutation carries
          // `meta.errorTitle`, so the global MutationCache toast fires. This
          // catch exists to release the row, not to hide the error.
        })
        .finally(() => setPending(null));
    },
    [message, pending],
  );

  if (!body.trim() && !onFork && !onStartTicket) return null;

  return (
    <div className="lg-chat-message-actions">
      <Button
        variant="plain"
        className="lg-chat-message-action"
        onClick={copy}
        disabled={!body.trim()}
        title="Copy this message"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <rect x="9" y="9" width="12" height="12" rx="2" />
          <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
        </svg>
        Copy
      </Button>
      {onFork ? (
        <button
          type="button"
          className="lg-chat-message-action"
          onClick={() => run("fork", onFork)}
          disabled={pending !== null}
          title="Branch the conversation from this reply"
        >
          {pending === "fork" ? "Forking…" : "Fork"}
        </button>
      ) : null}
      {onStartTicket ? (
        <OverflowMenu label="More actions for this reply" align="left" disabled={pending !== null}>
          <OverflowMenuItem
            onSelect={() => run("ticket", onStartTicket)}
            title="File this reply as a ticket and open it"
          >
            Start as ticket
          </OverflowMenuItem>
        </OverflowMenu>
      ) : null}
    </div>
  );
}

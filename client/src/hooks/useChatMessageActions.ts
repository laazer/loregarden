import { useMemo } from "react";

import type { ChatMessageActionHandlers } from "../components/chat/ChatMessageActions";
import { chatMessageBody, type ChatMessageView } from "../components/chat/chatUtils";
import { describeError, pushToast } from "../state/toastStore";
import type { ChatArchive } from "./useActiveChatSession";
import { useCreateChatTicket } from "./useCreateChatTicket";

/** Longest ticket title a reply is allowed to become. */
const MAX_TICKET_TITLE = 120;

/**
 * A ticket title out of a reply: its first line of real prose.
 *
 * Markdown chrome is stripped rather than carried into the title — a ticket
 * called "## Here is the plan" is a ticket nobody can scan in a list.
 */
export function ticketTitleFromReply(body: string): string {
  const line = body
    .split("\n")
    .map((candidate) => candidate.replace(/^\s*(?:[#>*-]+\s*|\d+[.)]\s*)/, "").trim())
    .find(Boolean);
  if (!line) return "";
  return line.length > MAX_TICKET_TITLE ? `${line.slice(0, MAX_TICKET_TITLE - 1).trimEnd()}…` : line;
}

/**
 * Fork and "start as ticket" for the replies in a thread.
 *
 * Both are omitted when their prerequisite is missing — no archive to branch,
 * no workspace to file into — so the row hides the control rather than offering
 * one that fails. Copy needs neither and is always in the row.
 */
export function useChatMessageActions({
  workspaceSlug,
  archive,
  enabled = true,
}: {
  workspaceSlug: string;
  archive: ChatArchive | null;
  /** False on a canned thread (the primitive gallery): nothing to branch or file. */
  enabled?: boolean;
}): ChatMessageActionHandlers {
  const createTicket = useCreateChatTicket(workspaceSlug);

  const startTicket = (message: ChatMessageView) => {
    const body = chatMessageBody(message).trim();
    const title = ticketTitleFromReply(body);
    // A reply that is only a card — a gate, a ticket list — has no prose to
    // name a ticket after. Say so rather than filing "Untitled".
    if (!title) {
      pushToast({
        title: "Start as ticket",
        message: "This reply has no text to make a ticket from.",
        tone: "error",
      });
      return Promise.resolve();
    }
    return createTicket.mutateAsync({ title, description: body });
  };

  const forkFromMessage = archive?.forkFromMessage;
  const canFork = enabled && Boolean(forkFromMessage && archive?.sessionId);
  const canFile = enabled && Boolean(workspaceSlug);

  return useMemo<ChatMessageActionHandlers>(
    () => ({
      onFork: canFork
        ? (message) =>
            forkFromMessage!(message.id).then(
              () =>
                pushToast({
                  title: "Forked",
                  message: "Branched from this reply — you are now on the copy.",
                  tone: "success",
                }),
              (error: unknown) =>
                pushToast({
                  title: "Fork chat",
                  message: describeError(error, "Could not branch this conversation"),
                  tone: "error",
                }),
            )
        : undefined,
      onStartTicket: canFile ? startTicket : undefined,
    }),
    // `startTicket` closes over a mutation whose identity is stable for the
    // workspace; re-created every render, it would churn the memo for nothing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [canFork, canFile, forkFromMessage, createTicket],
  );
}

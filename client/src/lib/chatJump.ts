import { pushToast } from "../state/toastStore";

/**
 * Scroll a chat turn into view and ring it briefly, for "Show in chat" from a
 * list of the thread's cards. Says so when the turn is not on screen any more
 * (an older page of the thread, or a conversation switched away from), rather
 * than doing nothing.
 */
export function showChatMessage(messageId: string, root: ParentNode = document): void {
  const node = root.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(messageId)}"]`);
  if (!node) {
    pushToast({
      tone: "warning",
      title: "Card not in view",
      message: "That turn isn't loaded in this thread any more.",
    });
    return;
  }
  node.scrollIntoView({ behavior: "smooth", block: "start" });
  node.classList.remove("lg-chat-turn--flash");
  // Restarts the highlight when the same card is picked twice in a row.
  void node.offsetWidth;
  node.classList.add("lg-chat-turn--flash");
}

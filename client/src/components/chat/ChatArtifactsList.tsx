import { PrimitiveSlot } from "./primitives/PrimitiveSlot";
import { renderChatPart } from "./primitives/registry";
import { primitiveKindLabel, type PrimitiveHistoryEntry } from "./primitiveHistory";
import { Button } from "../ui/Button";

import "./ChatArtifactsList.css";

/**
 * "What has Baxter made in this conversation?" — every card the chat
 * produced (tickets, plans, questions, boards, diffs), newest first and drawn
 * in full, so none has to be found by scrolling the thread. Each one leads
 * back to the turn that sent it.
 */
export function ChatArtifactsList({
  entries,
  onShowInChat,
  onSubmit,
}: {
  entries: readonly PrimitiveHistoryEntry[];
  onShowInChat: (messageId: string) => void;
  /** A card that asks something (a question, a plan step) answers through the chat. */
  onSubmit: (content: string) => void;
}) {
  if (entries.length === 0) {
    return (
      <p className="chat-artifacts-empty">
        Nothing made yet. Tickets, plans, questions and diffs Baxter creates in this chat collect
        here, newest first.
      </p>
    );
  }
  return (
    <ol className="chat-artifacts" aria-label={`${entries.length} artifacts in this chat`}>
      {entries.map((entry) => (
        <li key={entry.key} className="chat-artifact">
          <div className="chat-artifact-head">
            <span className="chat-artifact-kind">{primitiveKindLabel(entry.kind)}</span>
            {/* A card with no name of its own would only repeat its kind. */}
            <span className="chat-artifact-label" title={entry.label}>
              {entry.label === primitiveKindLabel(entry.kind) ? "" : entry.label}
            </span>
            <Button
              variant="plain"
              className="chat-artifact-show"
              aria-label={
                entry.label === primitiveKindLabel(entry.kind)
                  ? `Show this ${entry.label.toLowerCase()} in chat`
                  : `Show ${primitiveKindLabel(entry.kind)} ${entry.label} in chat`
              }
              onClick={() => onShowInChat(entry.messageId)}
            >
              Show in chat
            </Button>
          </div>
          <PrimitiveSlot kind={entry.part.primitive}>{renderChatPart(entry.part, entry.key, onSubmit)}</PrimitiveSlot>
        </li>
      ))}
    </ol>
  );
}

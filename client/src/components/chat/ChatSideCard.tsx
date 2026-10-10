import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { Button } from "../ui/Button";
import { ChatPromptRow } from "./ChatPromptRow";
import { primitiveKindLabel, type PrimitiveHistoryEntry } from "./primitiveHistory";
import "./ChatSideCard.css";

/**
 * The chat page's right-hand card: what to ask next, and an index of the cards
 * the thread has produced so far, which were otherwise only findable by
 * scrolling back.
 *
 * The prompts live here only while the card is open; the page draws them under
 * the composer when it is closed. Cards are an index, not a second copy: a
 * card drawn again at this width wrapped a word or two to a line, and the copy
 * in the thread is one click away.
 */
export function ChatSideCard({
  overlay,
  prompts,
  promptsDisabled,
  onPrompt,
  primitives,
  onJumpTo,
  onOpenGallery,
  onCollapse,
}: {
  /** A narrow window: the card covers the thread, so Escape and the scrim close it. */
  overlay: boolean;
  prompts: readonly string[];
  /** A turn is in flight, or no workspace answers yet. */
  promptsDisabled: boolean;
  onPrompt: (text: string) => void;
  primitives: PrimitiveHistoryEntry[];
  onJumpTo: (messageId: string) => void;
  onOpenGallery: () => void;
  onCollapse: () => void;
}) {
  useDialogDismiss(overlay ? onCollapse : null);
  // Picking a card or a prompt closes the overlay, or what it leads to lands behind it.
  const jumpTo = (messageId: string) => {
    if (overlay) onCollapse();
    onJumpTo(messageId);
  };
  const pickPrompt = (text: string) => {
    if (overlay) onCollapse();
    onPrompt(text);
  };
  return (
    <>
      {overlay ? (
        <Button
          variant="plain"
          className="chat-side-card-scrim"
          aria-label="Close the workbench"
          tabIndex={-1}
          onClick={onCollapse}
        />
      ) : null}
      <aside className="chat-side-card" aria-label="Workbench">
        <header className="chat-side-card-head">
          <h2>Workbench</h2>
          <Button
            variant="plain"
            className="chat-side-card-icon-btn"
            aria-label="Hide the workbench"
            title="Hide the workbench"
            onClick={onCollapse}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="m9 18 6-6-6-6" />
            </svg>
          </Button>
        </header>

        {prompts.length ? (
          <section className="chat-side-card-section" aria-labelledby="chat-side-prompts">
            <h3 id="chat-side-prompts">Suggestions</h3>
            <ChatPromptRow
              prompts={prompts}
              disabled={promptsDisabled}
              onPick={pickPrompt}
              stacked
            />
          </section>
        ) : null}

        <section className="chat-side-card-section" aria-labelledby="chat-side-primitives">
          <div className="chat-side-card-row">
            <h3 id="chat-side-primitives">Cards</h3>
            <Button variant="plain" className="chat-side-card-link" onClick={onOpenGallery}>
              Gallery
            </Button>
          </div>
          {primitives.length ? (
            // Newest first; each entry takes you to its card in the thread.
            <ol className="chat-side-history" aria-label={`${primitives.length} cards in this chat`}>
              {primitives.map((entry) => (
                <li key={entry.key}>
                  <Button
                    variant="plain"
                    className="chat-side-history-entry"
                    title="Show in thread"
                    onClick={() => jumpTo(entry.messageId)}
                  >
                    <span className="chat-side-history-kind">{primitiveKindLabel(entry.kind)}</span>
                    <span className="chat-side-history-label">{entry.label}</span>
                  </Button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="chat-side-card-empty">
              Tickets, plans, boards and diffs Baxter sends collect here, newest first. Open the
              gallery to see every kind.
            </p>
          )}
        </section>
      </aside>
    </>
  );
}

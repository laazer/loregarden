import { useMemo, useState } from "react";

import { PrimitiveParts } from "./primitives/PrimitiveParts";
import { primitiveSize } from "./primitives/primitiveFrame";
import { primitiveKindLabel, type PrimitiveHistoryEntry } from "./primitiveHistory";
import "./ChatSideCard.css";

/**
 * The chat page's right-hand card: what to ask next, and the cards the thread
 * has produced so far.
 *
 * Suggestions used to sit between the thread and the composer, where they
 * pushed the last reply up every time they appeared. Primitives were only
 * findable by scrolling back for them.
 */
export function ChatSideCard({
  suggestions,
  onSuggestion,
  suggestionsDisabled,
  primitives,
  onJumpTo,
  onPrimitiveSubmit,
  onOpenGallery,
  onCollapse,
}: {
  suggestions: readonly string[];
  onSuggestion: (text: string) => void;
  /** A turn is in flight; a suggestion now would be refused as a conflict. */
  suggestionsDisabled: boolean;
  primitives: PrimitiveHistoryEntry[];
  onJumpTo: (messageId: string) => void;
  onPrimitiveSubmit: (content: string) => void;
  onOpenGallery: () => void;
  onCollapse: () => void;
}) {
  // Newest by default, and it follows new cards until one is picked by hand.
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const shown = useMemo(
    () => primitives.find((entry) => entry.key === pickedKey) ?? primitives[0] ?? null,
    [primitives, pickedKey],
  );
  const fits = shown !== null && primitiveSize(shown.kind) === "regular";

  return (
    <aside className="chat-side-card" aria-label="Suggestions and cards">
      <header className="chat-side-card-head">
        <h2>Workbench</h2>
        <button
          type="button"
          className="chat-side-card-icon-btn"
          aria-label="Hide the workbench"
          title="Hide the workbench"
          onClick={onCollapse}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="m9 18 6-6-6-6" />
          </svg>
        </button>
      </header>

      <section className="chat-side-card-section" aria-labelledby="chat-side-suggestions">
        <h3 id="chat-side-suggestions">Suggestions</h3>
        <ul className="chat-side-suggestions">
          {suggestions.map((text) => (
            <li key={text}>
              <button
                type="button"
                className="chat-side-suggestion"
                disabled={suggestionsDisabled}
                title={suggestionsDisabled ? "Baxter is still answering" : undefined}
                onClick={() => onSuggestion(text)}
              >
                {text}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="chat-side-card-section" aria-labelledby="chat-side-primitives">
        <div className="chat-side-card-row">
          <h3 id="chat-side-primitives">Cards</h3>
          <button type="button" className="chat-side-card-link" onClick={onOpenGallery}>
            Gallery
          </button>
        </div>
        {shown ? (
          <>
            <div className="chat-side-card-stage">
              <div className="chat-side-card-stage-meta">
                <span>{primitiveKindLabel(shown.kind)}</span>
                {fits ? (
                  <button
                    type="button"
                    className="chat-side-card-link"
                    onClick={() => onJumpTo(shown.messageId)}
                  >
                    Show in thread
                  </button>
                ) : null}
              </div>
              {fits ? (
                <div className="chat-side-card-primitive">
                  <PrimitiveParts parts={[shown.part]} onSubmit={onPrimitiveSubmit} />
                </div>
              ) : (
                // Boards, trees and diffs are laid out for the thread's width;
                // squeezed into this column they wrap a word to a line.
                <div className="chat-side-card-summary">
                  <strong>{shown.label}</strong>
                  <span>Too wide for the workbench.</span>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    onClick={() => onJumpTo(shown.messageId)}
                  >
                    Open in thread
                  </button>
                </div>
              )}
            </div>
            <h4 className="chat-side-card-subhead">History · {primitives.length}</h4>
            <ol className="chat-side-history">
              {primitives.map((entry) => (
                <li key={entry.key}>
                  <button
                    type="button"
                    className="chat-side-history-entry"
                    aria-current={entry.key === shown.key ? "true" : undefined}
                    onClick={() => setPickedKey(entry.key)}
                  >
                    <span className="chat-side-history-kind">{primitiveKindLabel(entry.kind)}</span>
                    <span className="chat-side-history-label">{entry.label}</span>
                  </button>
                </li>
              ))}
            </ol>
          </>
        ) : (
          <p className="chat-side-card-empty">
            Tickets, plans, boards and diffs Baxter sends collect here, newest first. Open the
            gallery to see every kind.
          </p>
        )}
      </section>
    </aside>
  );
}

const PRIMITIVE_LABELS = [
  "Ticket",
  "Ticket workflow",
  "Parent ticket",
  "Ticket list",
  "Status column",
  "Kanban",
  "Filterable kanban",
  "Agent",
  "Workflow",
  "Gate",
  "Terminal",
  "Edit",
  "Thinking",
  "Calendar",
  "Event",
] as const;

export function ChatHistorySidebar({
  open,
  onClose,
  onOpenPrimitiveGallery,
}: {
  open: boolean;
  onClose: () => void;
  onOpenPrimitiveGallery: () => void;
}) {
  if (!open) return null;

  return (
    <>
      <button
        type="button"
        className="baxter-history-scrim"
        aria-label="Close chat history"
        onClick={onClose}
      />
      <aside className="baxter-history-panel" aria-label="Chat history">
        <header className="baxter-history-head">
          <div>
            <p className="baxter-history-eyebrow">Baxter archive</p>
            <h2>Chat history</h2>
          </div>
          <button
            type="button"
            className="baxter-history-close"
            aria-label="Close chat history"
            onClick={onClose}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </header>

        <div className="baxter-history-list">
          <button
            type="button"
            className="baxter-history-entry"
            onClick={onOpenPrimitiveGallery}
          >
            <span className="baxter-history-entry-mark" aria-hidden>
              UI
            </span>
            <span className="baxter-history-entry-copy">
              <span className="baxter-history-entry-row">
                <strong>UI Primitive gallery</strong>
                <time>Example</time>
              </span>
              <span className="baxter-history-entry-summary">
                One conversation showcasing every structured chat card.
              </span>
              <span className="baxter-history-tags">
                {PRIMITIVE_LABELS.map((label) => (
                  <span key={label}>{label}</span>
                ))}
              </span>
            </span>
            <svg className="baxter-history-entry-arrow" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="m9 18 6-6-6-6" />
            </svg>
          </button>
        </div>

        <p className="baxter-history-note">
          Baxter conversations are not persisted yet. This gallery remains available as a rendering reference.
        </p>
      </aside>
    </>
  );
}

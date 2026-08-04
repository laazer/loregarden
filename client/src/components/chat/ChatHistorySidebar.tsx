import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { baxterChatSessionsKey } from "../../hooks/useBaxterChatSession";
import { relativeTime } from "./relativeTime";

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
  workspaceSlug,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
}: {
  open: boolean;
  onClose: () => void;
  onOpenPrimitiveGallery: () => void;
  workspaceSlug: string;
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}) {
  const sessions = useQuery({
    queryKey: baxterChatSessionsKey(workspaceSlug),
    queryFn: () => api.baxterChatSessions(workspaceSlug),
    // Only fetched while the drawer is showing — an archive nobody is looking
    // at does not need to be current.
    enabled: open && Boolean(workspaceSlug),
    staleTime: 10_000,
  });

  if (!open) return null;

  const entries = sessions.data ?? [];

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
          {sessions.isLoading ? <p className="baxter-history-note">Loading conversations…</p> : null}
          {sessions.isError ? (
            <p className="baxter-history-note">Could not load your conversations.</p>
          ) : null}
          {!sessions.isLoading && !sessions.isError && entries.length === 0 ? (
            <p className="baxter-history-note">
              No conversations yet. Ask Baxter something and it will be saved here.
            </p>
          ) : null}

          {entries.map((entry) => (
            <div
              key={entry.id}
              className={`baxter-history-row${
                entry.id === activeSessionId ? " baxter-history-row--active" : ""
              }`}
            >
              <button
                type="button"
                className="baxter-history-entry"
                aria-current={entry.id === activeSessionId ? "true" : undefined}
                onClick={() => onSelectSession(entry.id)}
              >
                <span className="baxter-history-entry-copy">
                  <span className="baxter-history-entry-row">
                    <strong>{entry.title}</strong>
                    <time dateTime={entry.updated_at}>{relativeTime(entry.updated_at)}</time>
                  </span>
                  <span className="baxter-history-entry-summary">
                    {entry.preview || "No messages yet."}
                  </span>
                  <span className="baxter-history-entry-meta">
                    {entry.message_count} message{entry.message_count === 1 ? "" : "s"}
                  </span>
                </span>
              </button>
              <button
                type="button"
                className="baxter-history-delete"
                aria-label={`Delete ${entry.title}`}
                onClick={() => onDeleteSession(entry.id)}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                </svg>
              </button>
            </div>
          ))}

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
      </aside>
    </>
  );
}

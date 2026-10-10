import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { baxterChatSessionsKey } from "../../hooks/useBaxterChatSession";
import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
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
  const [query, setQuery] = useState("");
  // Deleting is permanent, so the trash control asks first; this is the row asking.
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const trapRef = useDialogFocusTrap<HTMLElement>();
  // Escape backs out of a pending delete before it closes the drawer.
  useDialogDismiss(open ? () => (confirmingId ? setConfirmingId(null) : onClose()) : null);

  if (!open) return null;

  const entries = sessions.data ?? [];
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? entries.filter((entry) =>
        `${entry.title}\n${entry.preview}`.toLowerCase().includes(needle),
      )
    : entries;

  return (
    <>
      <Button
        variant="plain"
        className="baxter-history-scrim"
        aria-label="Close chat history"
        tabIndex={-1}
        onClick={onClose}
      />
      <aside ref={trapRef} className="baxter-history-panel" aria-label="Chat history" tabIndex={-1}>
        <header className="baxter-history-head">
          <div>
            <p className="baxter-history-eyebrow">Baxter archive</p>
            <h2>Chat history</h2>
          </div>
          <Button
            variant="plain"
            className="baxter-history-close"
            aria-label="Close chat history"
            onClick={onClose}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </Button>
        </header>

        {entries.length ? (
          <div className="baxter-history-search">
            <Input
              type="search"
              aria-label="Search chats"
              placeholder="Search chats by title or last reply"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        ) : null}

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
          {needle && entries.length > 0 && shown.length === 0 ? (
            <p className="baxter-history-note">
              No chats match “{query.trim()}”.{" "}
              <Button variant="plain" className="baxter-history-clear" onClick={() => setQuery("")}>
                Clear search
              </Button>
            </p>
          ) : null}

          {shown.map((entry) => (
            <div
              key={entry.id}
              className={`baxter-history-row${
                entry.id === activeSessionId ? " baxter-history-row--active" : ""
              }`}
            >
              <Button
                variant="plain"
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
              </Button>
              {confirmingId === entry.id ? (
                <div className="baxter-history-confirm" role="group" aria-label={`Delete ${entry.title}?`}>
                  <span>Delete this chat for good?</span>
                  <Button
                    variant="plain"
                    className="baxter-history-confirm-delete"
                    onClick={() => {
                      setConfirmingId(null);
                      onDeleteSession(entry.id);
                    }}
                  >
                    Delete
                  </Button>
                  <Button
                    variant="plain"
                    className="baxter-history-confirm-cancel"
                    autoFocus
                    onClick={() => setConfirmingId(null)}
                  >
                    Cancel
                  </Button>
                </div>
              ) : (
                <Button
                  variant="plain"
                  className="baxter-history-delete"
                  aria-label={`Delete ${entry.title}`}
                  title="Delete this chat"
                  onClick={() => setConfirmingId(entry.id)}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                  </svg>
                </Button>
              )}
            </div>
          ))}

          <Button
            variant="plain"
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
          </Button>
        </div>
      </aside>
    </>
  );
}

import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { baxterChatSessionsKey } from "../../hooks/useBaxterChatSession";
import { relativeTime } from "./relativeTime";

/**
 * The chat archive in the dock's rail — the slot the openers use when the
 * thread is empty.
 *
 * A drawer would cover the conversation the operator is picking between, and
 * the dock is already only a few hundred pixels tall. So the archive takes the
 * space the openers had rather than opening over the turns.
 *
 * Contents only: the rail element itself belongs to the dock, which decides
 * whether this or the openers fill it.
 */
export function ChatHistoryRail({
  workspaceSlug,
  activeSessionId,
  onSelectSession,
}: {
  workspaceSlug: string;
  activeSessionId: string;
  onSelectSession: (id: string) => void;
}) {
  const sessions = useQuery({
    queryKey: baxterChatSessionsKey(workspaceSlug),
    queryFn: () => api.baxterChatSessions(workspaceSlug),
    // Mounted only while the rail is showing it, so no guard on visibility is
    // needed here — an archive nobody is looking at issues no request.
    enabled: Boolean(workspaceSlug),
    staleTime: 10_000,
  });

  const entries = sessions.data ?? [];

  return (
    <>
      <span className="copilot-dock-rail-label">Chat history</span>
      {sessions.isLoading ? (
        <p className="copilot-dock-rail-note">Loading conversations…</p>
      ) : null}
      {sessions.isError ? (
        <p className="copilot-dock-rail-note">Could not load your conversations.</p>
      ) : null}
      {!sessions.isLoading && !sessions.isError && entries.length === 0 ? (
        <p className="copilot-dock-rail-note">
          No conversations yet. Ask Baxter something and it will be saved here.
        </p>
      ) : null}

      {entries.map((entry) => (
        <button
          key={entry.id}
          type="button"
          className={`copilot-dock-rail-btn copilot-dock-history-btn${
            entry.id === activeSessionId ? " is-active" : ""
          }`}
          aria-current={entry.id === activeSessionId ? "true" : undefined}
          onClick={() => onSelectSession(entry.id)}
        >
          <span className="copilot-dock-history-copy">
            <span className="copilot-dock-history-row">
              <strong>{entry.title}</strong>
              <time dateTime={entry.updated_at}>{relativeTime(entry.updated_at)}</time>
            </span>
            <span className="copilot-dock-history-preview">
              {entry.preview || "No messages yet."}
            </span>
          </span>
        </button>
      ))}
    </>
  );
}

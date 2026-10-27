import type { KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";

import type { ChatAgents, ChatArchive } from "../hooks/useActiveChatSession";
import { useUiStore } from "../state/uiStore";
import { Button } from "./ui/Button";

/**
 * Baxter's mode, where a screen offers more than one: on an initiative, planning
 * its schedule or triaging its work.
 *
 * A radio group rather than a menu: there are two modes, each is one click, and
 * the choice is visible without opening anything. Arrow keys move between them
 * as a radio group's do. Held while a turn runs, so a reply never lands in a
 * thread the operator has switched away from.
 */
export function BaxterModeSwitch({ agents, disabled }: { agents: ChatAgents; disabled: boolean }) {
  const ids = agents.options.map((option) => option.id);
  const onKeyDown = (event: KeyboardEvent) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1
      : 0;
    if (!step || disabled) return;
    event.preventDefault();
    const next = ids[(ids.indexOf(agents.active) + step + ids.length) % ids.length];
    agents.select(next);
    document.getElementById(`copilot-dock-mode-${next}`)?.focus();
  };
  return (
    <div className="copilot-dock-modes" role="radiogroup" aria-label="Baxter mode" onKeyDown={onKeyDown}>
      {/* ux-ok: a fixed list of modes declared in useActiveChatSession, never
          fetched; the caller renders no switch at all when there is no choice. */}
      {agents.options.map((option) => {
        const active = option.id === agents.active;
        return (
          <Button
            key={option.id}
            variant="plain"
            id={`copilot-dock-mode-${option.id}`}
            className={`copilot-dock-mode${active ? " is-on" : ""}`}
            role="radio"
            aria-checked={active}
            aria-label={option.label}
            tabIndex={active ? 0 : -1}
            disabled={disabled && !active}
            title={disabled && !active ? "Wait for this turn to finish to switch modes" : option.description}
            onClick={() => agents.select(option.id)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}

/**
 * The dock's head: who is answering, what they are looking at, and the
 * conversation's own controls.
 *
 * Always "Baxter". Ticket triage, branch triage, the initiative planner and the
 * workspace thread are all Baxter to the operator, so the head names the
 * subject in a context line instead of naming a different assistant per page.
 */
export function CopilotDockHeader({
  titleId,
  context,
  agents,
  busy,
  archive,
  maximisePath,
}: {
  titleId: string;
  context: string;
  agents: ChatAgents | null;
  busy: boolean;
  archive: ChatArchive | null;
  maximisePath: string | null;
}) {
  const navigate = useNavigate();
  const historyOpen = useUiStore((s) => s.copilotHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setCopilotHistoryOpen);
  const setChatOpen = useUiStore((s) => s.setCopilotOpen);

  return (
    <header className="copilot-dock-head">
      <h2 id={titleId} className="copilot-dock-title">
        Baxter
      </h2>
      {context ? (
        <span className="copilot-dock-context" title={context}>
          {context}
        </span>
      ) : null}
      {agents ? <BaxterModeSwitch agents={agents} disabled={busy} /> : null}
      <span className="copilot-dock-head-spacer" aria-hidden />
      {/* Only a conversation with an archive has others to open or start: a
          ticket's triage chat is the ticket's, so these appear with the archive
          rather than as controls that do nothing. */}
      {archive ? (
        <>
          <Button
            variant="plain"
            className="copilot-dock-head-btn"
            aria-label="New chat"
            title="Start a new conversation with Baxter"
            onClick={() => {
              archive.startNewChat();
              setHistoryOpen(false);
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M12 5v14M5 12h14" />
            </svg>
          </Button>
          <Button
            variant="plain"
            className={`copilot-dock-head-btn${historyOpen ? " is-on" : ""}`}
            aria-label="History"
            aria-pressed={historyOpen}
            title="Open a past conversation"
            onClick={() => setHistoryOpen(!historyOpen)}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
              <path d="M3 3v5h5M12 7v5l3 2" />
            </svg>
          </Button>
        </>
      ) : null}
      {maximisePath ? (
        <Button
          variant="plain"
          className="copilot-dock-head-btn"
          aria-label="Open full page"
          title="Open this conversation as a full page"
          onClick={() => navigate(maximisePath)}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
          </svg>
        </Button>
      ) : null}
      <Button
        variant="plain"
        className="copilot-dock-head-btn"
        aria-label="Close Baxter"
        title="Close Baxter (the bar stays)"
        onClick={() => setChatOpen(false)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </Button>
    </header>
  );
}

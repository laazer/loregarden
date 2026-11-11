import { BaxterAvatar } from "./BaxterAvatar";
import { ChatPromptRow } from "./ChatPromptRow";
import { Button } from "../ui/Button";

/** What the conversation on each kind of screen is for, in one line. */
const SCOPE_LINE: Record<string, string> = {
  "ticket-triage": "Ask about this ticket: what is blocking it, or what its last run did.",
  "branch-triage": "Ask about this branch, or have Baxter commit and ship it.",
  "baxter-home": "Ask about anything in this workspace, or have Baxter do it.",
  "baxter-research":
    "Ask which projects do something. Baxter checks each claim in the project's source and records what it finds; it edits nothing.",
  "initiative-planner": "Ask about dates, pace, or what to start next.",
  "initiative-triage": "Ask about the work under this initiative.",
};

const DEFAULT_SCOPE_LINE = "Ask about what is on this screen.";

/**
 * An empty sidebar thread: who answers, what about, and the questions to start
 * with, where the turns will be.
 *
 * It replaced a lone "No messages yet." in the corner of the thread, with the
 * openers in a side rail that a sidebar is too narrow to show, so a right-docked
 * chat opened onto a blank column. The openers now sit in the thread itself, at
 * any width.
 *
 * `blocked` says why nothing can be sent (no workspace yet, say) and replaces
 * the openers, which could only fail. `loadError` is the conversation itself
 * failing to load, which must not read as an empty one.
 */
export function DockChatWelcome({
  assistantLabel,
  context,
  promptKind,
  prompts,
  promptsDisabled,
  blocked,
  loadError,
  onPick,
  onOpenHistory,
}: {
  assistantLabel: string;
  /** What the screen binds the chat to: a workspace, a ticket, a branch. */
  context: string;
  promptKind: string;
  prompts: readonly string[];
  /** A turn is in flight; a prompt now would be refused. */
  promptsDisabled: boolean;
  blocked: string | null;
  loadError: boolean;
  onPick: (prompt: string) => void;
  /** Present only where the conversation has an archive to pick from. */
  onOpenHistory?: () => void;
}) {
  if (loadError) {
    return (
      <div className="dock-welcome" role="alert">
        <p className="dock-welcome-title">Conversation unavailable</p>
        <p className="dock-welcome-text">
          This screen&apos;s conversation could not be loaded, so nothing can be sent to{" "}
          {assistantLabel} yet. Reload the page to try again.
        </p>
      </div>
    );
  }
  return (
    <div className="dock-welcome">
      <div className="dock-welcome-avatar">
        <BaxterAvatar variant="head" state="idle" size={44} label={assistantLabel} />
      </div>
      <div className="dock-welcome-intro">
        <p className="dock-welcome-title">
          Ask {assistantLabel}
          {context ? <span className="dock-welcome-context">{context}</span> : null}
        </p>
        <p className="dock-welcome-text">
          {blocked ?? SCOPE_LINE[promptKind] ?? DEFAULT_SCOPE_LINE}
        </p>
      </div>
      {blocked || prompts.length === 0 ? null : (
        <div className="dock-welcome-prompts">
          <span className="dock-welcome-label">Try asking</span>
          <ChatPromptRow prompts={prompts} disabled={promptsDisabled} onPick={onPick} stacked />
        </div>
      )}
      {onOpenHistory ? (
        <Button variant="plain" className="dock-welcome-history" onClick={onOpenHistory}>
          Pick up a past conversation
        </Button>
      ) : null}
    </div>
  );
}

/**
 * The action bar's small pieces: the one-click replies and the status pill. Split out of `AppActionBar`, which keeps the conversation
 * state and decides where each piece is drawn (the row or the sheet).
 */

import { useLocation, useNavigate } from "react-router-dom";

import type { ScreenPrompts } from "../../hooks/useActiveChatSession";
import { DOCK_QUICK_PROMPT_LIMIT, followUpPrompts } from "../../lib/dockChatPrompts";
import { Button } from "../ui/Button";

/** The bar's one-click replies, capped to what fits beside the composer. */
export function ActionBarQuickPrompts({
  prompts,
  disabled,
  onPick,
}: {
  prompts: readonly string[];
  disabled: boolean;
  onPick: (prompt: string) => void;
}) {
  const shown = prompts.slice(0, DOCK_QUICK_PROMPT_LIMIT);
  if (shown.length === 0) return null;
  return (
    <div className="app-action-bar-quick">
      {shown.map((prompt) => (
        <Button
          key={prompt}
          variant="plain"
          className="app-action-bar-quick-btn"
          title={prompt}
          disabled={disabled}
          onClick={() => onPick(prompt)}
        >
          {prompt}
        </Button>
      ))}
    </div>
  );
}

/**
 * Suggestions for the thread a page composes for: Home's Baxter thread.
 *
 * The page owns the composer, so the bar offers only the one-click replies.
 * Where another page draws the reply, a pick sends and lands there: from Home,
 * the answer would otherwise arrive somewhere nothing shows it. That page
 * draws its own row under its composer, so the bar draws none on it.
 */
export function ScreenQuickPrompts({ target }: { target: ScreenPrompts }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  if (target.replyPath !== null && pathname === target.replyPath) return null;
  const disabled = target.isBusy || target.unavailable;
  const pick = (prompt: string) => {
    if (disabled) return;
    if (target.replyPath !== null) navigate(target.replyPath);
    target.send(prompt);
  };
  return (
    <ActionBarQuickPrompts
      prompts={followUpPrompts(target.kind, null, target.messages)}
      disabled={disabled}
      onPick={pick}
    />
  );
}

/**
 * What the bar says about the bound conversation.
 *
 * A decision waiting on the operator outranks everything: an agent question
 * arrives as an approval rather than a message, so a bar showing only "working…"
 * would sit there with something unanswered.
 */
export function ActionBarStatus({
  label,
  waiting,
  loadError,
  sendError,
  busy,
}: {
  label: string;
  waiting: number;
  loadError: boolean;
  sendError: string | null;
  busy: boolean;
}) {
  if (waiting > 0) {
    return (
      <span className="app-action-bar-pill app-action-bar-pill--waiting">
        {waiting} waiting on you
      </span>
    );
  }
  if (loadError) {
    return (
      <span className="app-action-bar-pill app-action-bar-pill--error">
        conversation unavailable
      </span>
    );
  }
  if (sendError) {
    return <span className="app-action-bar-pill app-action-bar-pill--error">{sendError}</span>;
  }
  if (busy) {
    return <span className="app-action-bar-pill">working…</span>;
  }
  if (!label) return null;
  return (
    <span className="app-action-bar-pill app-action-bar-pill--context">
      <span className="app-action-bar-pill-dot" aria-hidden />
      On {label}
    </span>
  );
}

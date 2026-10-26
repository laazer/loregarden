import type { MouseEvent, ReactNode } from "react";

/**
 * What the bar holds besides the composer: a row that opens above the bar's
 * own while the composer (or anything in the sheet) has focus, and closes on
 * Escape or when focus leaves it. It takes room rather than floating over the
 * page, so it never covers the newest reply in Baxter's thread above the bar.
 *
 * The bar used to carry all of it on every page: 93px over two rows, 17
 * controls, most of them set once a conversation (the agent, the model,
 * auto-approve, History, New chat) or only wanted while writing (the quick
 * prompts, the run log, attachments). At rest the bar is now one 36px row, and
 * nothing is gone: it is all here, one focus away.
 *
 * It stays mounted while closed (`hidden`), so a dialog opened from it, the
 * model picker, survives focus moving into that dialog. A pointer press inside
 * keeps focus where it is (the composer) rather than letting the press blur it
 * and close the sheet under the click; keyboard users reach the controls with
 * Tab from the composer, which keeps it open because focus stays within.
 */
export function ActionBarSheet({
  id,
  open,
  controls,
  prompts,
  tray,
}: {
  id: string;
  open: boolean;
  /** The conversation's settings: agent, model, auto-approve, run logs, History, New chat. */
  controls: ReactNode;
  /** One-click replies to the turn on screen. */
  prompts: ReactNode;
  /** Files waiting to go out with the next message. */
  tray: ReactNode;
}) {
  const keepFocus = (event: MouseEvent<HTMLDivElement>) => event.preventDefault();
  return (
    <div
      id={id}
      className="app-action-bar-sheet"
      role="group"
      aria-label="Conversation options"
      hidden={!open}
      onMouseDown={keepFocus}
    >
      <div className="app-action-bar-sheet-row">{controls}</div>
      {prompts}
      {tray}
      <p className="app-action-bar-sheet-hint">Enter to send · / for commands · Esc to close</p>
    </div>
  );
}

import type { ChatWait } from "../api/chatTypes";

export type { ChatWait };

/**
 * Time left until the 2h wait ceiling: `Nh Nm left`, then `less than a minute left`.
 *
 * h = floor(s/3600), m = floor((s%3600)/60). Under 60s (or past the deadline)
 * is the short copy — never a negative duration.
 */
export function formatWaitRemaining(deadline: string, now: Date = new Date()): string {
  const remainingMs = new Date(deadline).getTime() - now.getTime();
  const remainingSeconds = Number.isFinite(remainingMs)
    ? Math.max(0, Math.floor(remainingMs / 1000))
    : 0;
  if (remainingSeconds < 60) return "less than a minute left";
  const hours = Math.floor(remainingSeconds / 3600);
  const minutes = Math.floor((remainingSeconds % 3600) / 60);
  return `${hours}h ${minutes}m left`;
}

/** Display form of a wait command: one line, whitespace collapsed. */
export function waitCommandOneLine(command: string): string {
  return command.replace(/\s+/g, " ").trim();
}

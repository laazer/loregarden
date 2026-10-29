/**
 * Status words Baxter writes in his own tables, read back as the shared chip.
 *
 * When a reply tabulates work ("aqueli | Reading", "Verzeta | Needs
 * permission"), the status column is the one the operator scans, and plain text
 * reads every state alike. A cell that is exactly one of these phrases renders
 * as the same chip the rest of the app uses for that meaning: running is a blue
 * dot, needs-you a violet diamond, failed a red square. Whole-cell matches
 * only, so prose that happens to contain "done" is never touched.
 */

import type { StatusTone } from "./statusTone";

const WORDS: Record<StatusTone, readonly string[]> = {
  run: ["running", "reading", "in progress", "working", "active", "started"],
  idle: ["queued", "pending", "idle", "not started", "scheduled"],
  you: ["needs permission", "needs you", "needs approval", "awaiting approval", "awaiting you", "waiting on you"],
  wait: ["blocked", "waiting", "paused", "parked"],
  bad: ["failed", "error", "errored", "broken"],
  done: ["done", "passed", "merged", "complete", "completed", "succeeded", "finished"],
};

const TONE_BY_WORD = new Map<string, StatusTone>(
  (Object.entries(WORDS) as [StatusTone, readonly string[]][]).flatMap(([tone, words]) =>
    words.map((word) => [word, tone] as const),
  ),
);

/** The chip tone for a cell that is exactly a status word, else null. */
export function statusToneForWord(text: string): StatusTone | null {
  return TONE_BY_WORD.get(text.trim().toLowerCase()) ?? null;
}

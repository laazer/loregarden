/**
 * Copy for the global action bar's composer and its quick prompts.
 *
 * Shared because the bar sends them and the expanded panel offers the same
 * openers above an empty thread — one list, or the two drift apart.
 */
export const COMPOSER_PLACEHOLDER: Record<string, string> = {
  "ticket-triage": "Message about this ticket…",
  "branch-triage": "Message about this branch…",
};

/**
 * Openers for the questions this dock is usually opened to ask.
 *
 * Prompt shortcuts, not suggestions: nothing infers them from the ticket.
 */
export const TRY_ASKING: Record<string, string[]> = {
  "ticket-triage": [
    "What is blocking this ticket?",
    "Summarise what the last run changed",
    "Why did the last stage fail?",
  ],
  "branch-triage": [
    "What changed on this branch?",
    "Is this branch safe to delete?",
    "commit and push",
  ],
};

/** Ship the work in front of you: the one opener that is an action, not a question. */
export const SHIP_PROMPT = "Commit, push, and open a PR";

const DEFAULT_BRANCHES = new Set(["main", "master"]);

/**
 * The openers for a conversation, with the shipping action first when the work
 * sits on a branch of its own.
 *
 * Withheld on the default branch: there is nothing to open a pull request
 * against from main, so the action would only ever fail.
 */
export function quickPrompts(kind: string, branch: string | null | undefined): string[] {
  const base = TRY_ASKING[kind] ?? [];
  if (!branch || DEFAULT_BRANCHES.has(branch)) return base;
  return [SHIP_PROMPT, ...base];
}

/** The bar has room for a couple of openers; the panel shows them all. */
export const DOCK_QUICK_PROMPT_LIMIT = 2;

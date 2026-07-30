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

/** The bar has room for a couple of openers; the panel shows them all. */
export const DOCK_QUICK_PROMPT_LIMIT = 2;

import type { Approval } from "../api/client";

/**
 * Whether an approval asks a human to verify something, rather than to wave a
 * tool call through.
 *
 * A stage sign-off carries the acceptance criteria the human reads before
 * approving; a testing checklist is the same ask arriving on any kind. Both
 * deserve the full-width Approvals tab — a permission prompt does not.
 */
export function hasHumanCriteria(approval: Approval): boolean {
  return approval.kind === "workflow_gate" || Boolean(approval.checklist?.length);
}

/** The gate brief's own heading for the criteria it restates from the ticket. */
const CRITERIA_HEADING = /^\s*#{0,6}\s*\**acceptance criteria\**\s*:?\s*$/im;

/**
 * A gate's impact ends by restating the ticket's acceptance criteria, which is
 * right in the inbox — the card is all the reader has — and redundant in the
 * Approvals tab, which lists those criteria above the card. Drops that trailing
 * section so the tab shows them once.
 *
 * Returns the impact unchanged when the heading is absent: the brief is written
 * by an agent, so its shape is a convention, not a guarantee, and dropping the
 * only copy would be worse than showing two.
 */
export function impactWithoutCriteria(impact: string): string {
  const match = CRITERIA_HEADING.exec(impact);
  if (!match) return impact;
  return impact.slice(0, match.index).trimEnd();
}

/** A criterion that opens with its own id — the short form a checklist can point at. */
const CRITERION_ID = /^(AC\d+)\s*:/i;

/**
 * Shortens checklist items that restate a criterion in full.
 *
 * The server expands a gate's `{{acceptance_criteria}}` placeholder into one
 * "Play-test by hand — <criterion>" item each, which is right where the card
 * stands alone. In the Approvals tab the criteria are listed above, so the item
 * only needs to name which one: "Play-test by hand — AC7".
 *
 * An item is shortened only when it ends with a criterion verbatim and that
 * criterion opens with an id to point at. Everything else — a hand-written
 * check, a criterion with no id — is left exactly as written.
 */
export function shortenRestatedChecklist(checklist: string[], criteria: string[]): string[] {
  const shortFormByText = new Map<string, string>();
  for (const criterion of criteria) {
    const text = criterion.trim();
    const id = CRITERION_ID.exec(text)?.[1];
    if (text && id) shortFormByText.set(text, id);
  }
  if (shortFormByText.size === 0) return checklist;

  return checklist.map((item) => {
    const trimmed = item.trim();
    for (const [text, id] of shortFormByText) {
      if (trimmed.length > text.length && trimmed.endsWith(text)) {
        return trimmed.slice(0, trimmed.length - text.length) + id;
      }
    }
    return item;
  });
}

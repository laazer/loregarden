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
  return (
    approval.kind === "workflow_gate" ||
    approval.kind === "rework_pause" ||
    // A parked stage is the same ask — read this, decide, and only a person can.
    // Filed with the permission prompts it would get a narrow card, and the
    // diagnosis it exists to show is the whole content.
    approval.kind === "stage_park" ||
    Boolean(approval.checklist?.length)
  );
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

/**
 * Whether a gate's checklist already carries every acceptance criterion.
 *
 * The server expands `{{acceptance_criteria}}` into one "Play-test by hand —
 * <criterion>" item each, so the checklist is the criteria in the form you can
 * actually work through. When it covers them all, a separate list of the same
 * text is the redundant copy — not the checklist, which is the useful one.
 */
export function checklistCoversCriteria(checklist: string[], criteria: string[]): boolean {
  const items = checklist.map((item) => item.trim()).filter(Boolean);
  const wanted = criteria.map((criterion) => criterion.trim()).filter(Boolean);
  if (wanted.length === 0 || items.length === 0) return false;
  return wanted.every((criterion) => items.some((item) => item.endsWith(criterion)));
}

/**
 * The siblings a sibling-overlap park would make this ticket wait for, or null
 * for any other approval.
 *
 * The server marks the park in `tool_name` and lists the siblings in
 * `tool_input_json` (`services/sibling_overlap_resolution.py`). Rejecting one
 * does not leave the stage parked: it makes the ticket wait for them to land.
 */
export function siblingOverlapWaitsOn(approval: Approval): string[] | null {
  if (approval.kind !== "stage_park" || approval.tool_name !== "sibling_overlap") return null;
  try {
    const payload = JSON.parse(approval.tool_input_json || "{}") as { sibling_external_ids?: unknown };
    const ids = payload.sibling_external_ids;
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string") : [];
  } catch {
    // silent-ok: the card still offers Wait, and the impact text names the siblings
    return [];
  }
}

/** What rejecting a park does, in the words its button uses. */
export function parkRejectLabel(approval: Approval): string {
  const waitsOn = siblingOverlapWaitsOn(approval);
  if (waitsOn === null) return "Leave parked";
  if (waitsOn.length === 1) return `Wait for ${waitsOn[0]}`;
  return waitsOn.length > 1 ? `Wait for ${waitsOn.length} siblings` : "Wait for siblings";
}

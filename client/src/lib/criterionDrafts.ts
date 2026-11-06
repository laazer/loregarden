/** The acceptance-criteria editor's rows, and the lists they save as. */

/**
 * The write-time cap, mirrored from `services.acceptance_criteria.MAX_ACCEPTANCE_CRITERIA`.
 *
 * Spelled here once, beside `draftCriteria`, because this is already the module
 * that mirrors the server's `normalize_criteria`, and the readout has to render
 * before any request. `test_ticket_size_guard.py::test_the_client_cap_constant_matches_the_servers`
 * reds if the two drift.
 */
export const MAX_ACCEPTANCE_CRITERIA = 12;

/** One row of the criteria editor. `key` is local and stable across renames. */
export interface CriterionDraft {
  key: number;
  text: string;
  checked: boolean;
}

let nextCriterionKey = 0;

export function newCriterionDraft(text = '', checked = false): CriterionDraft {
  nextCriterionKey += 1;
  return { key: nextCriterionKey, text, checked };
}

export function criteriaDrafts(criteria: string[], checked: string[]): CriterionDraft[] {
  const done = new Set(checked);
  return criteria.map((text) => newCriterionDraft(text, done.has(text)));
}

/** Trimmed, blanks dropped — mirrors the server's normalize_criteria. */
export function draftCriteria(rows: CriterionDraft[]): string[] {
  return rows.map((row) => row.text.trim()).filter((text) => text.length > 0);
}

export function draftChecked(rows: CriterionDraft[]): string[] {
  return rows.filter((row) => row.checked).map((row) => row.text.trim()).filter((text) => text.length > 0);
}

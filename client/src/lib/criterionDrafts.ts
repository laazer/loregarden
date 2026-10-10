/** The acceptance-criteria editor's rows, and the lists they save as. */

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

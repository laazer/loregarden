/**
 * The value a settings field holds while it is being edited, and how that
 * becomes the value the view stores.
 *
 * Separate from the form that renders it because the conversion is where the
 * only real decisions are — what an empty number box means, what a checkbox
 * that was never touched stores — and a pure function can be asked about them
 * directly. The form's own job is inputs and layout.
 */

import type { SettingsField } from "./primitives/types";

/**
 * A field's value mid-edit, which is not the value that is stored.
 *
 * A number field holds the input's raw text, because "" and "1e" are states a
 * user passes through on the way to a number and a numeric draft can represent
 * neither — a draft that coerced as it went would rewrite the box under the
 * caret. The text becomes a number once, at submit.
 */
export type DraftValue = string | boolean;

/** The stored value of `field`, as its input wants to receive it. */
function draftValue(field: SettingsField, stored: Record<string, unknown>): DraftValue {
  const value = stored[field.key];
  if (field.kind === "boolean") return typeof value === "boolean" ? value : field.default;
  if (field.kind === "number") {
    // A stored non-finite number is a value 444 already established the server
    // will hand back as `null`; the field opens on its default rather than on
    // "NaN", which is not a number a user can edit into one.
    return typeof value === "number" && Number.isFinite(value)
      ? String(value)
      : String(field.default);
  }
  return typeof value === "string" ? value : field.default;
}

/** The draft a form opens on: every declared field, seeded from what is stored. */
export function initialDraft(
  fields: SettingsField[],
  stored: Record<string, unknown>,
): Map<string, DraftValue> {
  return new Map(fields.map((field) => [field.key, draftValue(field, stored)]));
}

/** What a draft settled as: the values to store, and the fields that refused. */
export interface DraftReading {
  values: Map<string, unknown>;
  errors: Map<string, string>;
}

/** What a field that will not parse says, in the field, next to itself. */
export const NOT_A_NUMBER = "Enter a number.";

/**
 * Read a draft back as the values to store.
 *
 * One pass rather than a validate-then-convert pair: for a number the
 * conversion *is* the check, and doing it twice is two places for the two
 * answers to disagree.
 *
 * Only the *kind* of a value is checked here. Whether a workspace slug exists
 * or a ticket id resolves is not this form's question — the server does not
 * validate `settings` either, and a value naming something absent is supposed
 * to render the primitive's own empty state rather than block the edit.
 *
 * The number is the one refusal, and it is not an exception to that rule.
 * `NaN` serialises to `null`, which 444 established the server accepts in
 * silence; storing it would discard the field with no feedback anywhere. So it
 * is refused here, where the operator can still see what they typed.
 *
 * `Map`, not an object, on both sides: a field key is compared against declared
 * keys, and `"constructor" in plainObject` answers `true`.
 */
export function readDraft(
  fields: SettingsField[],
  draft: ReadonlyMap<string, DraftValue>,
): DraftReading {
  const values = new Map<string, unknown>();
  const errors = new Map<string, string>();

  for (const field of fields) {
    const raw = draft.get(field.key);
    if (field.kind === "boolean") {
      values.set(field.key, raw === true);
      continue;
    }
    if (field.kind === "number") {
      const text = typeof raw === "string" ? raw.trim() : "";
      const parsed = Number(text);
      // `Number("")` is 0, so an empty box would otherwise store a zero nobody
      // typed. It is a missing value, and it says so.
      if (text === "" || !Number.isFinite(parsed)) {
        errors.set(field.key, NOT_A_NUMBER);
        continue;
      }
      values.set(field.key, parsed);
      continue;
    }
    values.set(field.key, typeof raw === "string" ? raw : "");
  }
  return { values, errors };
}

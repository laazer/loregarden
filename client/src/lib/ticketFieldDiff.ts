/** One per-field comparison, used three times over, so it cannot disagree with itself.
 *
 * The ticket pane asks the same question in three places, and the whole 409
 * filter rests on all three answering it identically:
 *
 *   1. "Is the editor dirty?" — would a save send anything at all.
 *   2. "What goes in the patch?" — which fields the operator actually changed.
 *   3. "Did any of those move on the server while they were typing?" — the
 *      comparison that decides whether a revision conflict is the operator's
 *      problem or an agent run's bump they should never be shown.
 *
 * Written three times, the third answer drifts from the second and the pane
 * either shows a conflict block about fields nobody touched or resubmits over
 * the top of a real collision. Two concrete ways it drifts, both pinned by
 * tests: arrays compared with `!==` are always unequal because every fetch
 * deserialises fresh objects, and a value the operator *cleared* is falsy, so a
 * filter that skips falsy patch values compares nothing and blanks the field.
 *
 * So a snapshot is the one shape, taken from a ticket or from a save draft, and
 * `sameField` is the one comparison.
 */

import type { TicketDetail, TicketState } from '../api/client';

/** The seven editable fields, as one comparable shape. */
export interface TicketFieldSnapshot {
  title: string;
  description: string;
  acceptanceCriteria: string[];
  checkedAcceptanceCriteria: string[];
  tags: string[];
  state: TicketState;
  priority: number;
}

/** The patch keys the API takes for those seven fields. */
export type TicketPatchKey =
  | 'title'
  | 'description'
  | 'acceptance_criteria'
  | 'checked_acceptance_criteria'
  | 'tags'
  | 'state'
  | 'priority';

/** The patch body built from a snapshot pair, before `expected_revision`. */
export type TicketFieldPatch = Partial<{
  title: string;
  description: string;
  acceptance_criteria: string[];
  checked_acceptance_criteria: string[];
  tags: string[];
  state: TicketState;
  priority: number;
}>;

/** What the operator calls each field, for the conflict notice.
 *
 * Every label is digit-free on purpose: the notice names fields and never a
 * revision number, which at a mean of 826 and a max of 82,334 tells a person
 * nothing. */
export const TICKET_FIELD_LABELS: Record<TicketPatchKey, string> = {
  title: 'Title',
  description: 'Description',
  acceptance_criteria: 'Acceptance criteria',
  checked_acceptance_criteria: 'Checked criteria',
  tags: 'Tags',
  state: 'State',
  priority: 'Priority',
};

/** A field's value in a snapshot, and whether two of them are the same value. */
interface FieldSpec {
  key: TicketPatchKey;
  read: (snapshot: TicketFieldSnapshot) => string | number | string[];
  same: (a: TicketFieldSnapshot, b: TicketFieldSnapshot) => boolean;
}

/** Lists compare by contents, never by identity: both sides of every comparison
 * here came out of `JSON.parse`, so an untouched array is a different object. */
function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/** In patch order, which is the order the conflict notice reads its labels in. */
export const TICKET_FIELDS: readonly FieldSpec[] = [
  { key: 'title', read: (s) => s.title, same: (a, b) => a.title === b.title },
  { key: 'description', read: (s) => s.description, same: (a, b) => a.description === b.description },
  {
    key: 'acceptance_criteria',
    read: (s) => s.acceptanceCriteria,
    same: (a, b) => sameList(a.acceptanceCriteria, b.acceptanceCriteria),
  },
  {
    key: 'checked_acceptance_criteria',
    read: (s) => s.checkedAcceptanceCriteria,
    same: (a, b) => sameList(a.checkedAcceptanceCriteria, b.checkedAcceptanceCriteria),
  },
  { key: 'tags', read: (s) => s.tags, same: (a, b) => sameList(a.tags, b.tags) },
  { key: 'state', read: (s) => s.state, same: (a, b) => a.state === b.state },
  { key: 'priority', read: (s) => s.priority, same: (a, b) => a.priority === b.priority },
];

/** Which of `keys` hold a different value in `b` than in `a`.
 *
 * Restricted to the keys asked for, because the conflict notice may only name
 * the operator's own fields: a ticket whose priority a stage moved is not a
 * reason to tell them their title is at risk. */
export function movedFields(
  keys: readonly TicketPatchKey[],
  a: TicketFieldSnapshot,
  b: TicketFieldSnapshot,
): TicketPatchKey[] {
  const asked = new Set(keys);
  return TICKET_FIELDS.filter((field) => asked.has(field.key) && !field.same(a, b)).map(
    (field) => field.key,
  );
}

/** The patch that takes `base` to `next`: only the fields that differ. */
export function ticketFieldPatch(
  base: TicketFieldSnapshot,
  next: TicketFieldSnapshot,
): TicketFieldPatch {
  const patch: Record<string, string | number | string[]> = {};
  for (const field of TICKET_FIELDS) {
    if (!field.same(base, next)) patch[field.key] = field.read(next);
  }
  return patch as TicketFieldPatch;
}

/** The keys `ticketFieldPatch` would carry, in patch order. */
export function ticketPatchKeys(
  base: TicketFieldSnapshot,
  next: TicketFieldSnapshot,
): TicketPatchKey[] {
  return TICKET_FIELDS.filter((field) => !field.same(base, next)).map((field) => field.key);
}

/** Comma-separated, blanks and case-insensitive duplicates dropped — mirrors
 * services/ticket_tags.normalize_tags, which has the last word on what is stored. */
export function parseTicketTags(text: string): string[] {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const raw of text.split(',')) {
    const tag = raw.trim();
    if (!tag) continue;
    const key = tag.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    tags.push(tag);
  }
  return tags;
}

function asText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  return String(value);
}

function asTextList(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => asText(item));
  return [asText(value)];
}

/** A snapshot of what the server holds, normalised the same way a draft is.
 *
 * Tags go through `parseTicketTags` on both sides, so a stored `" Backend"` and
 * an operator who typed `"Backend"` are the same value rather than a phantom
 * edit the pane would then claim had moved. */
export function ticketSnapshot(ticket: TicketDetail): TicketFieldSnapshot {
  const criteria = asTextList(ticket.acceptance_criteria);
  return {
    title: asText(ticket.title).trim(),
    description: asText(ticket.description),
    acceptanceCriteria: criteria,
    // The server drops checks whose text is not a criterion; comparing against
    // what it would keep stops that pruning from reading as an edit.
    checkedAcceptanceCriteria: asTextList(ticket.checked_acceptance_criteria).filter((check) =>
      criteria.includes(check),
    ),
    tags: parseTicketTags(asTextList(ticket.tags).join(',')),
    state: ticket.state,
    priority: ticket.priority,
  };
}

/** One field moved reads as a sentence; several read as a list.
 *
 * "Title changed while you were editing." / "Title and Description changed…" /
 * "Title, Description and Tags changed…" — a comma list with "and" before the
 * last, which for exactly two is no comma. */
export function describeMovedFields(keys: readonly TicketPatchKey[]): string {
  const labels = keys.map((key) => TICKET_FIELD_LABELS[key]);
  if (labels.length === 0) {
    return 'Something else changed this ticket while you were editing.';
  }
  const list =
    labels.length === 1
      ? labels[0]
      : `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
  return `${list} changed while you were editing.`;
}

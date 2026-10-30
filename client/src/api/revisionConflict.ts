/** Reading a revision conflict out of a failed request.
 *
 * `errorStatus(err) === 409` is deliberately not the test. Several endpoints
 * refuse with a 409 — a branch already attached to another ticket, a dependency
 * that would make a cycle — and those carry a string `detail` and no revision
 * at all. A pane that treated the status as the signal would show the operator
 * a conflict block with two buttons for a problem neither of them addresses,
 * and would have nothing to compare or resubmit against.
 *
 * So the signal is the body: `error === "revision_conflict"` plus two integers.
 * That shape is the server's `TicketRevisionConflict.payload`, and it reaches
 * here through `ApiError.payload` — the whole parsed body, which for an
 * HTTPException is `{detail: {...}}`.
 */

import { errorStatus } from '../state/toastStore';

/** The server's revision_conflict payload, as the pane needs it. */
export interface RevisionConflict {
  expectedRevision: number;
  currentRevision: number;
  message: string;
}

function detailOf(error: unknown): Record<string, unknown> | null {
  const payload = (error as { payload?: unknown } | null)?.payload;
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  return detail as Record<string, unknown>;
}

/** The conflict a failed save carried, or null if it was any other failure. */
export function revisionConflictOf(error: unknown): RevisionConflict | null {
  if (errorStatus(error) !== 409) return null;
  const detail = detailOf(error);
  if (!detail || detail.error !== 'revision_conflict') return null;
  const expected = detail.expected_revision;
  const current = detail.current_revision;
  if (typeof expected !== 'number' || typeof current !== 'number') return null;
  return {
    expectedRevision: expected,
    currentRevision: current,
    message: typeof detail.message === 'string' ? detail.message : '',
  };
}

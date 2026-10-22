/** "Did my edit land, or did something else overwrite it while I was typing?"
 *
 * The question an operator asks at the moment they click Save in the ticket
 * details modal, and this block is the only place the pane answers it. It
 * appears where `saveError` already sits, above the edit form, and it is amber
 * rather than red because it is a question with two answers, not a failure.
 *
 * What it must never do:
 *
 * - **Print the revision.** Measured on the live database read-only on
 *   2026-10-20: mean `revision` 826, max 82,334, 1,257-4,333 bumps per agent
 *   run. It is a log-line counter, not an edit counter, and the number tells a
 *   person nothing. The block names fields, by the label the form uses.
 * - **Appear for a bump that touched nothing of theirs.** The pane filters that
 *   before rendering this; see `DashboardTicketDetailsButton`.
 * - **Clear the draft.** The operator's text is still in the inputs behind this
 *   block, and the only thing that discards it is their own click on "Discard
 *   my text and load latest".
 *
 * Two buttons, both named for what they do, so neither needs a second confirm
 * step. This mirrors the conflict precedent at `TicketGithubIssue` ("Keep
 * ticket's version" / "Keep GitHub's version").
 *
 * Its own file because `TicketDetailsModal.tsx` is already 600+ lines and the
 * size gate runs on the whole staged file. No entrance animation: an alert the
 * operator has to read should not be delayed by motion.
 */

import { useEffect, useRef } from 'react';

import { describeMovedFields, type TicketPatchKey } from '../lib/ticketFieldDiff';
import { Button } from './ui/Button';

export interface TicketConflictNoticeProps {
  /** The operator's own fields that moved on the server, in form order. */
  movedFields: readonly TicketPatchKey[];
  /** A resubmit or a re-check is in flight: neither button may fire again. */
  isResolving: boolean;
  /** Send the same patch again, against the revision just fetched. */
  onKeep: () => void;
  /** Throw the draft away and reseed it from what the server now holds. */
  onDiscard: () => void;
}

export function TicketConflictNotice({
  movedFields,
  isResolving,
  onKeep,
  onDiscard,
}: TicketConflictNoticeProps) {
  const blockRef = useRef<HTMLDivElement | null>(null);

  // Focus moves here when it appears, so a screen reader announces it and Tab
  // from this point reaches the two buttons rather than the form behind them.
  useEffect(() => {
    blockRef.current?.focus();
  }, []);

  return (
    <div
      ref={blockRef}
      role="alert"
      tabIndex={-1}
      className="ticket-conflict-notice"
      data-testid="ticket-conflict-notice"
    >
      <p className="ticket-conflict-notice-text">{describeMovedFields(movedFields)}</p>
      <p className="ticket-conflict-notice-text">
        Your text is still here, untouched. Save it over theirs, or load what they wrote.
      </p>
      <div className="ticket-conflict-notice-actions">
        <Button variant="secondary" compact disabled={isResolving} onClick={onKeep}>
          Keep my text
        </Button>
        <Button variant="secondary" compact disabled={isResolving} onClick={onDiscard}>
          Discard my text and load latest
        </Button>
      </div>
    </div>
  );
}

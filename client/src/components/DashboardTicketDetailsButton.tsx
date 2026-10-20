import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  TicketDetailsModal,
  type TicketDetailsSaveDraft,
  type TicketDraftSeed,
} from './TicketDetailsModal';
import { describeError } from '../state/toastStore';
import { revisionConflictOf } from '../api/revisionConflict';
import {
  movedFields,
  ticketFieldPatch,
  ticketPatchKeys,
  ticketSnapshot,
  type TicketFieldPatch,
  type TicketPatchKey,
} from '../lib/ticketFieldDiff';
import * as apiClient from '../api/client';

export interface DashboardTicketDetailsButtonProps {
  ticketId: string;
  className?: string;
}

/** A refused save, and everything needed to answer it without asking again. */
interface ConflictState {
  /** The patch the operator submitted, verbatim; a resubmit sends exactly this. */
  patch: TicketFieldPatch;
  /** Its keys, which are the only fields the notice may name. */
  keys: TicketPatchKey[];
  seed: TicketDraftSeed;
  /** The revision just fetched: what a resubmit carries. */
  revision: number;
  /** Of `keys`, the ones that moved between the seed and that fetch. */
  moved: TicketPatchKey[];
}

/** What a guarded save ended as. A conflict and a failed check both *resolve*
 * the mutation: neither is an error the global toast should report, and a
 * conflict in particular is a question the inline block asks, not a failure. */
type SaveOutcome =
  | { kind: 'saved' }
  | { kind: 'nothing' }
  | { kind: 'conflict'; conflict: ConflictState; fresh: apiClient.TicketDetail }
  | { kind: 'check_failed'; error: unknown };

/** What a conflict whose follow-up refetch itself failed tells the operator.
 * Not a conflict — no comparison was made — so it goes down the saveError path. */
const CHECK_FAILED_HINT =
  'Could not check whether this ticket changed while you were editing, so nothing was saved. Your text is still here — try saving again.';

export const DashboardTicketDetailsButton: React.FC<DashboardTicketDetailsButtonProps> = ({
  ticketId,
  className = '',
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | undefined>();
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  // Bumped when a resubmit lands, so the modal closes its editor as it would
  // after an ordinary save; the save that resolved never went through its form.
  const [closeEditorNonce, setCloseEditorNonce] = useState(0);
  const qc = useQueryClient();

  const { data: ticketDetail, isLoading, error } = useQuery({
    queryKey: ['ticket', ticketId],
    queryFn: () => apiClient.api.ticket(ticketId),
    enabled: isModalOpen,
  });

  /** Submit a patch under the revision guard, and read a refusal.
   *
   * `autoResubmits` is the bound C4 asks for: at most one automatic retry per
   * save. `revision` runs at a mean of 826 with 1,257-4,333 bumps per agent
   * run, so "retry until it lands" is a loop with no exit, and an unfiltered
   * mismatch would interrupt nearly every save made while a run is live. So a
   * refusal is filtered: refetch, compare only the fields this patch carried
   * against the snapshot the draft was seeded from, and resubmit silently when
   * none of them moved. Only a real collision becomes the operator's problem.
   */
  const submitGuarded = async (
    patch: TicketFieldPatch,
    keys: TicketPatchKey[],
    seed: TicketDraftSeed,
    startRevision: number,
    autoResubmits: number,
  ): Promise<SaveOutcome> => {
    let revision = startRevision;
    let resubmitsLeft = autoResubmits;
    for (;;) {
      try {
        await apiClient.api.updateTicket(ticketId, { ...patch, expected_revision: revision });
        return { kind: 'saved' };
      } catch (err) {
        // Not every 409 is this: a branch already attached to another ticket
        // shares the status and carries no revision, and belongs on the error
        // path where the operator is told what actually refused them.
        if (!revisionConflictOf(err)) throw err;
        let fresh: apiClient.TicketDetail;
        try {
          fresh = await apiClient.api.ticket(ticketId);
        } catch (checkError) {
          return { kind: 'check_failed', error: checkError };
        }
        const moved = movedFields(keys, seed.fields, ticketSnapshot(fresh));
        revision = fresh.revision;
        if (moved.length === 0 && resubmitsLeft > 0) {
          resubmitsLeft -= 1;
          continue;
        }
        return { kind: 'conflict', conflict: { patch, keys, seed, revision, moved }, fresh };
      }
    }
  };

  /** A refusal's own refetch is the newest the pane has seen; make it the
   * cache's, so the metadata pane is current and "load latest" has it to load. */
  const settleOutcome = (outcome: SaveOutcome) => {
    if (outcome.kind === 'saved') {
      setSaveError(undefined);
      setConflict(null);
      qc.invalidateQueries({ queryKey: ['ticket', ticketId] });
      qc.invalidateQueries({ queryKey: ['ticket-tree'] });
      qc.invalidateQueries({ queryKey: ['tickets'] });
      return;
    }
    if (outcome.kind === 'conflict') {
      setSaveError(undefined);
      qc.setQueryData(['ticket', ticketId], outcome.fresh);
      setConflict(outcome.conflict);
      return;
    }
    if (outcome.kind === 'check_failed') {
      setConflict(null);
      setSaveError(`${CHECK_FAILED_HINT} (${describeError(outcome.error, 'the ticket could not be re-read')})`);
    }
  };

  const saveDetails = useMutation({
    meta: { errorTitle: "Save ticket details" },
    mutationFn: async (draft: TicketDetailsSaveDraft): Promise<SaveOutcome> => {
      // Diffed against the snapshot the draft was seeded from, not the live
      // query data: that query refetches on every invalidation from anywhere in
      // this pane, and diffing against a row the operator never read sends the
      // other writer's fields back out as the operator's own.
      const next = {
        title: draft.title,
        description: draft.description,
        acceptanceCriteria: draft.acceptanceCriteria,
        checkedAcceptanceCriteria: draft.checkedAcceptanceCriteria,
        tags: draft.tags,
        state: draft.state,
        priority: draft.priority,
      };
      const keys = ticketPatchKeys(draft.seed.fields, next);
      if (keys.length === 0) return { kind: 'nothing' };
      const patch = ticketFieldPatch(draft.seed.fields, next);
      return submitGuarded(patch, keys, draft.seed, draft.seed.revision, 1);
    },
    onSuccess: settleOutcome,
    onError: (err) => {
      setConflict(null);
      setSaveError(describeError(err, 'Failed to save ticket details'));
    },
  });

  /** "Keep my text": the same patch again, against the revision just fetched.
   * No automatic retry — the operator is already in the loop, and a second
   * refusal is theirs to answer rather than one for the pane to race. */
  const keepMyText = useMutation({
    meta: { errorTitle: "Save ticket details" },
    mutationFn: (state: ConflictState): Promise<SaveOutcome> =>
      submitGuarded(state.patch, state.keys, state.seed, state.revision, 0),
    onSuccess: (outcome) => {
      settleOutcome(outcome);
      if (outcome.kind === 'saved') setCloseEditorNonce((n) => n + 1);
    },
    onError: (err) => {
      setConflict(null);
      setSaveError(describeError(err, 'Failed to save ticket details'));
    },
  });

  // Ticking a criterion from read mode: one field, saved on the click. No
  // `expected_revision`: there is no unsaved text to protect, the server's reply
  // is authoritative via setQueryData below, and a token here would conflict
  // against the bump firehose with nothing for the operator to resolve.
  const saveChecks = useMutation({
    meta: { errorTitle: "Check off acceptance criterion" },
    mutationFn: (checked: string[]) =>
      apiClient.api.updateTicket(ticketId, { checked_acceptance_criteria: checked }),
    onSuccess: (updated) => {
      setSaveError(undefined);
      qc.setQueryData(['ticket', ticketId], updated);
    },
    onError: (err) => {
      setSaveError(describeError(err, 'Failed to save the checked criteria'));
    },
  });

  const handleOpenModal = () => {
    setSaveError(undefined);
    setConflict(null);
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setSaveError(undefined);
    setConflict(null);
  };

  // Both reject on failure, after onError has put the message in `saveError`,
  // which TicketDetailsModal renders; the rejection is how it knows to keep
  // the draft (or revert the checkbox) rather than assume the write landed.
  const handleSave = async (draft: TicketDetailsSaveDraft) => {
    setSaveError(undefined);
    setConflict(null);
    const outcome = await saveDetails.mutateAsync(draft);
    // A conflict and a failed check are not mutation errors — nothing should
    // toast — but they are not a save either, so the editor has to stay open
    // with the draft in it. Rejecting here is how the modal is told.
    if (outcome.kind === 'conflict' || outcome.kind === 'check_failed') {
      throw new Error('the ticket was not saved');
    }
  };

  const handleSaveChecks = async (checked: string[]) => {
    setSaveError(undefined);
    await saveChecks.mutateAsync(checked);
  };

  return (
    <>
      <button
        onClick={handleOpenModal}
        className={`btn-secondary btn-compact dashboard-details-btn ${className}`}
        aria-label="View ticket details"
        type="button"
        disabled={isLoading && isModalOpen}
      >
        {isLoading && isModalOpen ? (
          <>
            <span className="dashboard-details-spinner" aria-hidden="true" />
            <span>Loading...</span>
          </>
        ) : (
          <>
            <svg className="dashboard-details-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>Details</span>
          </>
        )}
      </button>

      <TicketDetailsModal
        ticket={ticketDetail || null}
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        isLoading={isLoading}
        error={error ? (error instanceof Error ? error.message : 'Failed to load ticket details') : undefined}
        isSaving={saveDetails.isPending}
        saveError={saveError}
        onSave={handleSave}
        onSaveChecks={handleSaveChecks}
        isSavingChecks={saveChecks.isPending}
        conflict={
          conflict
            ? {
                movedFields: conflict.moved,
                isResolving: keepMyText.isPending,
                onKeep: () => keepMyText.mutate(conflict),
                onDiscard: () => setConflict(null),
                onDismiss: () => setConflict(null),
              }
            : null
        }
        closeEditorNonce={closeEditorNonce}
      />
    </>
  );
};

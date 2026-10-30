import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import * as apiClient from "../api/client";
import { revisionConflictOf } from "../api/revisionConflict";
import type { TicketDetailsSaveDraft, TicketDraftSeed } from "../components/TicketDetailsModal";
import {
  movedFields,
  ticketFieldPatch,
  ticketPatchKeys,
  ticketSnapshot,
  type TicketFieldPatch,
  type TicketPatchKey,
} from "../lib/ticketFieldDiff";
import { describeError } from "../state/toastStore";

/** A refused save, and everything needed to answer it without asking again. */
export interface TicketConflictState {
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
  | { kind: "saved" }
  | { kind: "nothing" }
  | { kind: "conflict"; conflict: TicketConflictState; fresh: apiClient.TicketDetail }
  | { kind: "check_failed"; error: unknown };

/** What a conflict whose follow-up refetch itself failed tells the operator.
 * Not a conflict — no comparison was made — so it goes down the saveError path. */
const CHECK_FAILED_HINT =
  "Could not check whether this ticket changed while you were editing, so nothing was saved. Your text is still here — try saving again.";

/**
 * Everything `TicketDetailsModal` needs for one ticket: the fetch, the edit and
 * the read-mode check-off, with failures held as `saveError` for the modal to
 * render and a refused revision held as `conflict` for it to put to the
 * operator. `ticketId` may be the UUID or the shareable id; writes go to the
 * UUID once the ticket has loaded. Nothing is fetched while `enabled` is false.
 */
export function useTicketDetailsEditor(ticketId: string | null, enabled: boolean) {
  const [saveError, setSaveError] = useState<string | undefined>();
  const [conflict, setConflict] = useState<TicketConflictState | null>(null);
  // Bumped when a resubmit lands, so the modal closes its editor as it would
  // after an ordinary save; the save that resolved never went through its form.
  const [closeEditorNonce, setCloseEditorNonce] = useState(0);
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => apiClient.api.ticket(ticketId ?? ""),
    enabled: enabled && Boolean(ticketId),
  });
  const ticket = query.data ?? null;
  // Writes and a refusal's re-read both address the row, not the shareable id
  // the pane may have been opened with.
  const writeId = ticket?.id ?? ticketId ?? "";

  /** A freshly-read row is the newest the pane has seen; cache it under both
   * keys, so a pane opened by shareable id sees it too. */
  const cacheFresh = (fresh: apiClient.TicketDetail) => {
    qc.setQueryData(["ticket", ticketId], fresh);
    if (fresh.id !== ticketId) qc.setQueryData(["ticket", fresh.id], fresh);
  };

  const refresh = (updated?: apiClient.TicketDetail) => {
    if (updated) cacheFresh(updated);
    void qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
    if (ticket && ticket.id !== ticketId) void qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
  };

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
        await apiClient.api.updateTicket(writeId, { ...patch, expected_revision: revision });
        return { kind: "saved" };
      } catch (err) {
        // Not every 409 is this: a branch already attached to another ticket
        // shares the status and carries no revision, and belongs on the error
        // path where the operator is told what actually refused them.
        if (!revisionConflictOf(err)) throw err;
        let fresh: apiClient.TicketDetail;
        try {
          fresh = await apiClient.api.ticket(writeId);
        } catch (checkError) {
          return { kind: "check_failed", error: checkError };
        }
        const moved = movedFields(keys, seed.fields, ticketSnapshot(fresh));
        revision = fresh.revision;
        if (moved.length === 0 && resubmitsLeft > 0) {
          resubmitsLeft -= 1;
          continue;
        }
        return { kind: "conflict", conflict: { patch, keys, seed, revision, moved }, fresh };
      }
    }
  };

  /** A refusal's own refetch is the newest the pane has seen; make it the
   * cache's, so the metadata pane is current and "load latest" has it to load. */
  const settleOutcome = (outcome: SaveOutcome) => {
    if (outcome.kind === "saved") {
      setSaveError(undefined);
      setConflict(null);
      refresh();
      void qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      return;
    }
    if (outcome.kind === "conflict") {
      setSaveError(undefined);
      cacheFresh(outcome.fresh);
      setConflict(outcome.conflict);
      return;
    }
    if (outcome.kind === "check_failed") {
      setConflict(null);
      setSaveError(
        `${CHECK_FAILED_HINT} (${describeError(outcome.error, "the ticket could not be re-read")})`,
      );
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
      if (keys.length === 0) return { kind: "nothing" };
      const patch = ticketFieldPatch(draft.seed.fields, next);
      return submitGuarded(patch, keys, draft.seed, draft.seed.revision, 1);
    },
    onSuccess: settleOutcome,
    onError: (err) => {
      setConflict(null);
      setSaveError(describeError(err, "Failed to save ticket details"));
    },
  });

  /** "Keep my text": the same patch again, against the revision just fetched.
   * No automatic retry — the operator is already in the loop, and a second
   * refusal is theirs to answer rather than one for the pane to race. */
  const keepMyText = useMutation({
    meta: { errorTitle: "Save ticket details" },
    mutationFn: (state: TicketConflictState): Promise<SaveOutcome> =>
      submitGuarded(state.patch, state.keys, state.seed, state.revision, 0),
    onSuccess: (outcome) => {
      settleOutcome(outcome);
      if (outcome.kind === "saved") setCloseEditorNonce((n) => n + 1);
    },
    onError: (err) => {
      setConflict(null);
      setSaveError(describeError(err, "Failed to save ticket details"));
    },
  });

  // Ticking a criterion from read mode: one field, saved on the click. No
  // `expected_revision`: there is no unsaved text to protect, the server's reply
  // is authoritative via setQueryData below, and a token here would conflict
  // against the bump firehose with nothing for the operator to resolve.
  const saveChecks = useMutation({
    meta: { errorTitle: "Check off acceptance criterion" },
    mutationFn: (checked: string[]) =>
      apiClient.api.updateTicket(writeId, { checked_acceptance_criteria: checked }),
    onSuccess: (updated) => {
      setSaveError(undefined);
      refresh(updated);
    },
    onError: (err) => setSaveError(describeError(err, "Failed to save the checked criteria")),
  });

  // Both reject on failure, after onError has put the message in `saveError`,
  // which TicketDetailsModal renders; the rejection is how it knows to keep
  // the draft (or revert the checkbox) rather than assume the write landed.
  return {
    ticket,
    isLoading: query.isLoading,
    loadError: query.error ? describeError(query.error, "Failed to load ticket details") : undefined,
    saveError,
    clearSaveError: () => {
      setSaveError(undefined);
      setConflict(null);
    },
    isSaving: saveDetails.isPending,
    isSavingChecks: saveChecks.isPending,
    /** The refused save to put to the operator, shaped for the modal's notice. */
    conflict: conflict
      ? {
          movedFields: conflict.moved,
          isResolving: keepMyText.isPending,
          onKeep: () => keepMyText.mutate(conflict),
          onDiscard: () => setConflict(null),
          onDismiss: () => setConflict(null),
        }
      : null,
    closeEditorNonce,
    save: async (draft: TicketDetailsSaveDraft) => {
      setSaveError(undefined);
      setConflict(null);
      const outcome = await saveDetails.mutateAsync(draft);
      // A conflict and a failed check are not mutation errors — nothing should
      // toast — but they are not a save either, so the editor has to stay open
      // with the draft in it. Rejecting here is how the modal is told.
      if (outcome.kind === "conflict" || outcome.kind === "check_failed") {
        throw new Error("the ticket was not saved");
      }
    },
    saveChecks: async (checked: string[]) => {
      setSaveError(undefined);
      await saveChecks.mutateAsync(checked);
    },
  };
}

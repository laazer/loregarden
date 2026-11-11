import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { RESEARCH_CLAIM_ROOT, RESEARCH_QUESTION_ROOT } from "../../lib/researchCandidates";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";

interface RefuteClaimModalProps {
  open: boolean;
  url: string;
  /** What the project is said to do, in the question's words. */
  capability: string;
  /** The project's name, for the title; the URL when it has none. */
  name: string;
  questionId?: string | null;
  onClose: () => void;
}

/**
 * A person's correction: this project does not do this. It is recorded as a
 * refutation that stands over later agent claims, so the reason is required:
 * it is what the next research turn is shown.
 */
export function RefuteClaimModal({ open, url, capability, name, questionId, onClose }: RefuteClaimModalProps) {
  const titleId = useId();
  const noteId = useId();
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();
  const refute = useMutation({
    mutationFn: () => api.refuteResearchClaim({ url, capability, note: note.trim(), question_id: questionId ?? null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [RESEARCH_CLAIM_ROOT] });
      void queryClient.invalidateQueries({ queryKey: [RESEARCH_QUESTION_ROOT] });
      pushToast({ tone: "success", title: "Refutation recorded", message: `${name}: not “${capability}”` });
      setNote("");
      onClose();
    },
  });
  const tooShort = note.trim().length < 3;

  return (
    <ModalShell open={open} onDismiss={refute.isPending ? undefined : onClose} labelledBy={titleId}>
      <div className="modal-header">
        <div>
          <div className="state-label">Research claim</div>
          <h2 id={titleId} className="modal-title">
            Refute this claim
          </h2>
          <p className="modal-subtitle">
            {name} does not “{capability}”. Research will show this to every later turn and will not
            recommend {name} for it without new source evidence.
          </p>
        </div>
      </div>
      <div className="modal-body">
        <label htmlFor={noteId} className="rs-field-label">
          Why (what you know that the claim missed)
        </label>
        <Textarea
          id={noteId}
          className="rs-note"
          rows={3}
          value={note}
          maxLength={600}
          disabled={refute.isPending}
          onChange={(event) => setNote(event.target.value)}
        />
        {refute.error ? (
          <p className="rs-error" role="alert">
            The refutation was not recorded: {describeError(refute.error, "request failed")}
          </p>
        ) : null}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" disabled={refute.isPending} onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" disabled={tooShort || refute.isPending} onClick={() => refute.mutate()}>
          {refute.isPending ? "Recording…" : "Record refutation"}
        </Button>
      </div>
    </ModalShell>
  );
}

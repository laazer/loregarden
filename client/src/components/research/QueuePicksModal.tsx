import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { EntryArea } from "../../api/referenceAreasApi";
import type { BulkAddItem } from "../../api/referenceLibraryApi";
import { RESEARCH_PROJECT_ROOT, RESEARCH_QUESTION_ROOT } from "../../lib/researchCandidates";
import { describeError, pushToast } from "../../state/toastStore";
import { AreaPicker } from "../memory/ReferenceAreaPicker";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";

export interface QueuePick {
  url: string;
  name: string;
}

interface QueuePicksModalProps {
  open: boolean;
  picks: readonly QueuePick[];
  /** The research question they answer; the survey pass is briefed with it. */
  questionId: string | null;
  question: string;
  onClose: () => void;
  /** After a queue that left nothing failed; the caller clears its selection. */
  onQueued?: () => void;
}

/** Outcomes that put the project in front of a survey pass, or already had it there. */
const OK_OUTCOMES: ReadonlySet<BulkAddItem["outcome"]> = new Set([
  "queued",
  "proposed",
  "attached",
  "rescoped",
  "already",
]);

/**
 * Queue research picks for a survey pass. A person choosing them is their
 * admission, so they join the review queue at once; each is linked to the
 * question, so its pass is briefed with the question and the claims to confirm.
 */
export function QueuePicksModal({ open, picks, questionId, question, onClose, onQueued }: QueuePicksModalProps) {
  const titleId = useId();
  const [areas, setAreas] = useState<EntryArea[]>([]);
  const [failed, setFailed] = useState<BulkAddItem[]>([]);
  const queryClient = useQueryClient();
  const queue = useMutation({
    mutationFn: () =>
      api.queueResearchPicks({
        urls: picks.map((pick) => pick.url),
        question_id: questionId,
        areas: areas.map((area) => area.area),
      }),
    onSuccess: (items) => {
      void queryClient.invalidateQueries({ queryKey: [RESEARCH_QUESTION_ROOT] });
      void queryClient.invalidateQueries({ queryKey: [RESEARCH_PROJECT_ROOT] });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      const bad = items.filter((item) => !OK_OUTCOMES.has(item.outcome));
      setFailed(bad);
      const queued = items.length - bad.length;
      if (queued) {
        pushToast({
          tone: "success",
          title: `Queued ${queued} for review`,
          message: "Each survey pass is briefed with this question and its claims.",
        });
      }
      if (bad.length === 0) {
        setAreas([]);
        onQueued?.();
        onClose();
      }
    },
  });
  const count = picks.length;

  return (
    <ModalShell open={open} onDismiss={queue.isPending ? undefined : onClose} labelledBy={titleId}>
      <div className="modal-header">
        <div>
          <div className="state-label">Reference library</div>
          <h2 id={titleId} className="modal-title">
            Queue {count} for review
          </h2>
          <p className="modal-subtitle">
            {question
              ? `Each joins the review queue now, and its survey pass is asked: “${question}”`
              : "Each joins the review queue now."}
          </p>
        </div>
      </div>
      <div className="modal-body">
        <ul className="rs-pick-list" aria-label="Projects to queue">
          {picks.map((pick) => (
            <li key={pick.url}>
              <strong>{pick.name || pick.url}</strong> <span className="rs-summary rs-inline">{pick.url}</span>
            </li>
          ))}
        </ul>
        <p className="rs-field-label">Project areas they serve (optional)</p>
        <AreaPicker
          picked={areas}
          onChange={setAreas}
          name={count === 1 ? picks[0].name || picks[0].url : `${count} projects`}
          disabled={queue.isPending}
        />
        {queue.error ? (
          <p className="rs-error" role="alert">
            Nothing was queued: {describeError(queue.error, "request failed")}
          </p>
        ) : null}
        {failed.length ? (
          <div className="rs-error" role="alert">
            <p>Not queued:</p>
            <ul>
              {failed.map((item) => (
                <li key={item.url}>
                  {item.url}: {item.detail || item.outcome}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" disabled={queue.isPending} onClick={onClose}>
          {failed.length ? "Close" : "Cancel"}
        </Button>
        <Button variant="primary" disabled={queue.isPending || count === 0} onClick={() => queue.mutate()}>
          {queue.isPending ? "Queueing…" : `Queue ${count} for review`}
        </Button>
      </div>
    </ModalShell>
  );
}

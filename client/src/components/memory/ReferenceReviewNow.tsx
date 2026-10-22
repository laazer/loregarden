import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { ReviewNowRequest, ReviewNowResult } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { IconCloseButton } from "../IconCloseButton";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";
import { ReferenceTargetsPicker } from "./ReferenceTargetsPicker";

function alsoText(result: ReviewNowResult): string {
  const parts = [];
  if (result.workspaces_added.length) parts.push(`it now reads for ${result.workspaces_added.join(", ")} too`);
  if (result.initiatives_linked.length) parts.push(`${result.initiatives_linked.join(", ")} now follow it`);
  return parts.length ? ` ${parts.join("; ")}.` : "";
}

function report(name: string, result: ReviewNowResult) {
  if (result.started) {
    pushToast({
      tone: "success",
      title: `Reviewing ${name}`,
      message: `Pass ${result.ordinal} is queued to run${result.carrier ? ` as ${result.carrier}` : ""}.${alsoText(result)}`,
    });
  } else if (result.refused) {
    pushToast({ tone: "warning", title: `${name} was not reviewed`, message: result.refused });
  } else {
    pushToast({
      tone: "warning",
      title: `${name} is first in line but has not started`,
      message: `Pass ${result.ordinal} is waiting: ${result.waiting.replace(/`/g, "")}.${alsoText(result)}`,
    });
  }
}

const NOTHING_MORE: ReviewNowRequest = { question: "", workspace_slugs: [], initiative_ids: [] };

function useReviewNow(sourceId: string, name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ReviewNowRequest) => api.reviewSourceNow(sourceId, body),
    onSuccess: (result) => {
      report(name, result);
      void queryClient.invalidateQueries({ queryKey: ["reference-source", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: `Could not review ${name} now`, message: describeError(error) }),
  });
}

/**
 * Dispatch this source's waiting pass now instead of at its turn. It goes
 * first in line either way; the toast says whether it started or what holds it.
 */
export function ReviewNowButton({
  sourceId,
  name,
  variant = "primary",
}: {
  sourceId: string;
  name: string;
  variant?: "primary" | "secondary";
}) {
  const review = useReviewNow(sourceId, name);
  return (
    <Button variant={variant} compact disabled={review.isPending} onClick={() => review.mutate(NOTHING_MORE)}>
      {review.isPending ? "Starting…" : "Review now"}
    </Button>
  );
}

/**
 * Review a source now, choosing who it is for: more workspaces to read for and
 * initiatives its tickets join. A surveyed source also takes the question its
 * new pass answers (its own question to start); a queued pass keeps its own.
 */
export function ReviewForm({
  sourceId,
  name,
  question,
  served,
  following,
  openLabel,
  refusedBecause,
}: {
  sourceId: string;
  name: string;
  /** The question to start from, or null when a queued pass already has one. */
  question: string | null;
  served: string[];
  /** Initiative ids already following the source. */
  following: string[];
  openLabel: string;
  /** Set for a refused source: reviewing it again needs a reason, shown beside this. */
  refusedBecause?: string;
}) {
  const review = useReviewNow(sourceId, name);
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(question ?? "");
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [initiatives, setInitiatives] = useState<string[]>([]);
  const ids = { title: useId(), question: useId(), reason: useId() };
  const [reason, setReason] = useState("");
  const needsReason = refusedBecause !== undefined;
  const needsQuestion = question !== null;
  function close() {
    setOpen(false);
    setWorkspaces([]);
    setInitiatives([]);
  }
  return (
    <>
      <Button variant="primary" compact aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        {openLabel}
      </Button>
      <ModalShell open={open} onDismiss={review.isPending ? undefined : close} labelledBy={ids.title} panelClassName="modal-panel-wide">
        <form
          aria-label={`Review ${name} now`}
          onSubmit={(event) => {
            event.preventDefault();
            if (review.isPending) return;
            review.mutate(
              {
                question: needsQuestion ? asked.trim() : "",
                workspace_slugs: workspaces,
                initiative_ids: initiatives,
                ...(needsReason ? { reason: reason.trim() } : {}),
              },
              { onSuccess: close },
            );
          }}
        >
          <div className="modal-header">
            <div>
              <h2 id={ids.title} className="modal-title">
                Review {name} now
              </h2>
              <p className="modal-subtitle">
                It reads for every workspace and initiative ticked{needsQuestion ? "; a question narrows it" : ""}.
              </p>
            </div>
            <IconCloseButton disabled={review.isPending} onClick={close} />
          </div>
          <div className="modal-body rl-modal-body">
            {needsReason ? (
              <>
                <p className="rl-refusal">It was refused: {refusedBecause || "no reason recorded"}</p>
                <label htmlFor={ids.reason}>Why should it be read after all?</label>
                <Textarea
                  id={ids.reason}
                  rows={2}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="They relicensed to MIT in v2."
                  required
                />
              </>
            ) : null}
            <ReferenceTargetsPicker
              fixedWorkspaces={served}
              workspaces={workspaces}
              onWorkspaces={setWorkspaces}
              fixedInitiatives={following}
              initiatives={initiatives}
              onInitiatives={setInitiatives}
            />
            {needsQuestion ? (
              <>
                <label htmlFor={ids.question}>What should this review answer? (optional)</label>
                <Textarea id={ids.question} rows={2} value={asked} onChange={(event) => setAsked(event.target.value)} />
              </>
            ) : null}
          </div>
          <div className="modal-footer">
            <Button variant="secondary" disabled={review.isPending} onClick={close}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={review.isPending || (needsReason && !reason.trim())}>
              {review.isPending ? "Starting…" : "Review now"}
            </Button>
          </div>
        </form>
      </ModalShell>
    </>
  );
}

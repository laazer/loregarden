import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { ReviewNowResult } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Textarea } from "../ui/Textarea";

function report(name: string, result: ReviewNowResult) {
  if (result.started) {
    pushToast({
      tone: "success",
      title: `Reviewing ${name}`,
      message: `Pass ${result.ordinal} is queued to run${result.carrier ? ` as ${result.carrier}` : ""}.`,
    });
  } else if (result.refused) {
    pushToast({ tone: "warning", title: `${name} was not reviewed`, message: result.refused });
  } else {
    pushToast({
      tone: "warning",
      title: `${name} is first in line but has not started`,
      message: `Pass ${result.ordinal} is waiting: ${result.waiting.replace(/`/g, "")}.`,
    });
  }
}

function useReviewNow(sourceId: string, name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (question: string) => api.reviewSourceNow(sourceId, question),
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
    <Button variant={variant} compact disabled={review.isPending} onClick={() => review.mutate("")}>
      {review.isPending ? "Starting…" : "Review now"}
    </Button>
  );
}

/**
 * Review a surveyed source again: a new pass reads the whole tree, within its
 * file budget, against the question given (the source's own question to start).
 */
export function ReviewAgain({ sourceId, name, question }: { sourceId: string; name: string; question: string }) {
  const review = useReviewNow(sourceId, name);
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(question);
  const fieldId = useId();
  if (!open) {
    return (
      <Button variant="secondary" compact aria-expanded={false} onClick={() => setOpen(true)}>
        Review again now…
      </Button>
    );
  }
  return (
    <form
      className="rl-review-again"
      onSubmit={(event) => {
        event.preventDefault();
        review.mutate(asked.trim(), { onSuccess: () => setOpen(false) });
      }}
    >
      <label htmlFor={fieldId}>What should this review answer?</label>
      <Textarea
        id={fieldId}
        rows={2}
        value={asked}
        onChange={(event) => setAsked(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            setOpen(false);
          }
        }}
      />
      <div className="rl-form-actions">
        <Button type="submit" variant="primary" compact disabled={review.isPending || !asked.trim()}>
          {review.isPending ? "Starting…" : "Review now"}
        </Button>
        <Button variant="plain" compact aria-expanded onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

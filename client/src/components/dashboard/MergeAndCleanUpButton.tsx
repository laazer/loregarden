import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import { ticketPullRequestKey, type MergeAndCleanUp, type PullRequestStatus } from "../../api/ticketPullRequestApi";
import { NotThisTarget } from "../../lib/agentActions/registry";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { toastActionFailed } from "../../state/toastStore";
import { Button } from "../ui/Button";

/**
 * "Merge and clean up", offered only when GitHub says the PR can merge now.
 *
 * A merge cannot be taken back, so the first click asks; the second merges,
 * pinned to the head commit on screen (the server refuses if the PR moved).
 * Afterwards each cleanup step is listed with what happened, because a step
 * that could not finish (a worktree with unsaved work) is the operator's to
 * finish by hand. The results stay after the PR flips to merged.
 *
 * Agents reach the same merge as `ticket.merge_pull_request` while the button is
 * offered (a write: gated like any write tool), or without a tab through
 * `loregarden_merge_pull_request`. Either way the server re-checks the PR first.
 */
export function MergeAndCleanUpButton({ ticketId, pr }: { ticketId: string; pr: PullRequestStatus }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const merge = useMutation({
    mutationFn: (headSha: string) => api.mergeTicketPullRequest(ticketId, { number: pr.number, head_sha: headSha }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ticketPullRequestKey(ticketId) });
      void qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
    },
    onError: (error) => toastActionFailed(`Merge #${pr.number}`, error),
    onSettled: () => setConfirming(false),
  });

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  useAgentAction(
    "ticket.merge_pull_request",
    async ({ ticket_id, number, head_sha }) => {
      if (ticket_id !== ticketId || number !== pr.number) {
        throw new NotThisTarget(`this tab shows #${pr.number} on ticket ${ticketId}; open the ticket first`);
      }
      return merge.mutateAsync(head_sha);
    },
    pr.mergeable_now && !merge.data && !merge.isPending,
  );

  if (merge.data) return <MergeResult result={merge.data} />;
  if (!pr.mergeable_now) return null;

  if (!confirming) {
    return (
      <div className="prp-actions prp-actions--start">
        <Button variant="primary" onClick={() => setConfirming(true)}>
          Merge and clean up
        </Button>
      </div>
    );
  }
  return (
    <div
      className="prp-merge-confirm"
      role="group"
      aria-label={`Confirm merging #${pr.number}`}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !merge.isPending) setConfirming(false);
      }}
    >
      <p className="prp-merge-question">
        Squash-merge #{pr.number} into <code>{pr.base}</code>, then delete <code>{pr.head}</code> here and on
        GitHub? This cannot be undone.
      </p>
      <div className="prp-actions prp-actions--start">
        <Button ref={confirmRef} variant="primary" disabled={merge.isPending} onClick={() => merge.mutate(pr.head_sha)}>
          {merge.isPending ? "Merging…" : `Merge #${pr.number}`}
        </Button>
        <Button variant="secondary" disabled={merge.isPending} onClick={() => setConfirming(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function MergeResult({ result }: { result: MergeAndCleanUp }) {
  const unfinished = result.steps.filter((step) => !step.ok).length;
  return (
    <section className="prp-merge-result" role="status" aria-label={`Merged #${result.number}`}>
      <p className={`prp-verdict prp-verdict--${unfinished ? "warn" : "good"}`}>
        Merged #{result.number}
        {result.merge_commit ? ` as ${result.merge_commit.slice(0, 7)}` : ""}.
        {unfinished ? ` ${unfinished} cleanup step${unfinished === 1 ? "" : "s"} need you.` : " Cleaned up."}
      </p>
      {result.steps.length > 0 && (
        <ul className="prp-checks">
          {result.steps.map((step) => (
            <li key={step.step} className={`prp-check prp-check--${step.ok ? "passing" : "failing"}`}>
              <span className="prp-check-icon" aria-hidden="true">
                {step.ok ? "✓" : "✕"}
              </span>
              <span className="prp-check-name">{step.step}</span>
              <span className="prp-outcome">{step.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

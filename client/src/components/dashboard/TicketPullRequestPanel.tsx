import { useMutation, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { api } from "../../api/client";
import {
  ticketPullRequestKey,
  type PullRequestCheck,
  type PullRequestCheckOutcome,
  type PullRequestReview,
  type PullRequestStatus,
  type TicketPullRequest,
} from "../../api/ticketPullRequestApi";
import { useUiStore } from "../../state/uiStore";
import { describeError, toastActionFailed } from "../../state/toastStore";
import { MarkdownContent } from "../chat/MarkdownContent";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { MergeAndCleanUpButton } from "./MergeAndCleanUpButton";
import "./ArtifactPane.css";
import "./TicketPullRequestPanel.css";

/** Pending checks settle in minutes; re-ask GitHub on that scale, not faster. */
const PENDING_POLL_MS = 30_000;

const OUTCOME_ORDER: Record<PullRequestCheckOutcome, number> = { failing: 0, pending: 1, passing: 2, skipped: 3 };
const OUTCOME_LABEL: Record<PullRequestCheckOutcome, string> = {
  failing: "Failing",
  pending: "Running",
  passing: "Passed",
  skipped: "Skipped",
};
/** Decorative: the outcome is also spelled out beside it. */
const OUTCOME_GLYPH: Record<PullRequestCheckOutcome, string> = {
  failing: "✕",
  pending: "•",
  passing: "✓",
  skipped: "–",
};
const TALLY_ORDER: readonly PullRequestCheckOutcome[] = ["failing", "pending", "passing", "skipped"];
const REVIEW_LABEL: Record<PullRequestReview, string> = {
  approved: "Approved",
  changes_requested: "Changes requested",
  review_required: "Review required",
  not_required: "No review required",
};

function stateBadge(pr: PullRequestStatus): { label: string; tone: string } {
  if (pr.state === "merged") return { label: "Merged", tone: "merged" };
  if (pr.state === "closed") return { label: "Closed", tone: "closed" };
  if (pr.is_draft) return { label: "Draft", tone: "draft" };
  return { label: "Open", tone: "open" };
}

function countBy(checks: readonly PullRequestCheck[], outcome: PullRequestCheckOutcome): number {
  return checks.filter((check) => check.outcome === outcome).length;
}

/** The one-line answer to "can this merge, and if not, what is in the way?". */
function verdict(pr: PullRequestStatus): { text: string; tone: "good" | "bad" | "warn" } {
  if (pr.state === "merged") return { text: `Merged into ${pr.base}.`, tone: "good" };
  if (pr.state === "closed") return { text: "Closed without merging.", tone: "warn" };
  const failing = pr.checks.filter((check) => check.outcome === "failing");
  if (failing.length === 1) return { text: `${failing[0].name} is failing.`, tone: "bad" };
  if (failing.length) return { text: `${failing.length} checks failing.`, tone: "bad" };
  if (pr.has_conflicts) return { text: `Conflicts with ${pr.base}; the branch needs a rebase.`, tone: "bad" };
  if (pr.review === "changes_requested") return { text: "A reviewer requested changes.", tone: "bad" };
  const pending = countBy(pr.checks, "pending");
  if (pending) return { text: `${pending} check${pending === 1 ? "" : "s"} still running.`, tone: "warn" };
  if (pr.is_draft) return { text: "Draft: mark it ready for review on GitHub.", tone: "warn" };
  if (pr.review === "review_required") return { text: "Checks pass; waiting on a review.", tone: "warn" };
  if (pr.unsigned_commits.length) {
    const n = pr.unsigned_commits.length;
    return {
      text: `${n} commit${n === 1 ? " is" : "s are"} unsigned, and ${pr.base} requires signed commits.`,
      tone: "bad",
    };
  }
  if (!pr.mergeable_now) {
    // GitHub's branch rules can still hold it (signatures, a required check not
    // yet reported); saying "ready" here sent people to a merge button that refused.
    return { text: `Checks pass, but GitHub's rules for ${pr.base} still block the merge.`, tone: "warn" };
  }
  return { text: "Checks pass; ready to merge.", tone: "good" };
}

function fixChecksPrompt(pr: PullRequestStatus): string {
  const failing = pr.checks.filter((check) => check.outcome === "failing");
  return [
    `CI is failing on PR #${pr.number} (${pr.url}):`,
    "",
    ...failing.map((check) => `- ${check.name}${check.url ? `: ${check.url}` : ""}`),
    "",
    "Read the failing logs, find the first real failure, fix it on this ticket's branch, and push.",
  ].join("\n");
}

/**
 * The PR tab: "is this ticket's pull request ready to merge, and if not, what
 * is blocking it?"
 *
 * Asks GitHub live rather than reading the `pr` artifact, which only exists
 * for PRs opened from this tab; the old view said "No pull request opened"
 * beside an open PR with a failing check.
 */
export function TicketPullRequestPanel({
  ticketId,
  onOpenPr,
  isOpeningPr,
  onCommitPush,
  isCommittingPush,
}: {
  ticketId: string;
  onOpenPr?: () => void;
  isOpeningPr: boolean;
  onCommitPush?: () => void;
  isCommittingPush: boolean;
}) {
  const lookup = useQuery({
    queryKey: ticketPullRequestKey(ticketId),
    queryFn: () => api.ticketPullRequest(ticketId),
    staleTime: 15_000,
    refetchInterval: (query) => {
      const pr = query.state.data?.pull_request;
      return pr?.state === "open" && pr.checks.some((check) => check.outcome === "pending") ? PENDING_POLL_MS : false;
    },
  });

  if (lookup.isPending) return <PaneSkeleton variant="list" rows={5} label="Asking GitHub about this ticket's pull request…" />;
  if (lookup.isError) {
    return (
      <div className="ap-state" role="alert">
        <div className="ap-state-title">Could not load this ticket&rsquo;s pull request</div>
        <div className="ap-state-body">{describeError(lookup.error, "The pull request request failed.")}</div>
        <Button variant="secondary" compact onClick={() => void lookup.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const data = lookup.data;
  const refresh = (
    <Button
      variant="secondary"
      compact
      disabled={lookup.isFetching}
      onClick={() => void lookup.refetch()}
    >
      {lookup.isFetching ? "Checking…" : "Refresh"}
    </Button>
  );

  if (data.lookup === "failed") return <LookupFailed data={data} refresh={refresh} />;
  if ((data.lookup === "none" || !data.pull_request) && data.ships_with) {
    return (
      <div className="ap-state">
        <div className="ap-state-title">Ships in its tree&rsquo;s pull request</div>
        <div className="ap-state-body">
          This ticket&rsquo;s work lands on <code className="prp-branch">{data.ships_with}</code>, which goes to the
          base as one PR when the tree&rsquo;s root completes. A PR for its own branch would carry the same commits
          twice.
        </div>
        <div className="prp-actions">{refresh}</div>
      </div>
    );
  }
  if (data.lookup === "none" || !data.pull_request) {
    return (
      <div className="ap-state">
        <div className="ap-state-title">No pull request for this branch yet</div>
        <div className="ap-state-body">
          GitHub has no PR for <code className="prp-branch">{data.branch}</code>. Push the ticket&rsquo;s work and open
          one to get it reviewed and merged.
        </div>
        <div className="prp-actions">
          {onCommitPush && (
            <Button variant="secondary" disabled={isCommittingPush} onClick={onCommitPush}>
              {isCommittingPush ? "Committing…" : "Commit & push"}
            </Button>
          )}
          {onOpenPr && (
            <Button variant="primary" disabled={isOpeningPr} onClick={onOpenPr}>
              {isOpeningPr ? "Opening PR…" : "Open PR"}
            </Button>
          )}
          {refresh}
        </div>
      </div>
    );
  }
  return <PullRequestSummary ticketId={ticketId} pr={data.pull_request} refresh={refresh} />;
}

function LookupFailed({ data, refresh }: { data: TicketPullRequest; refresh: ReactNode }) {
  return (
    <div className="ap-state" role="alert">
      <div className="ap-state-title">Could not ask GitHub about this branch&rsquo;s pull request</div>
      <div className="ap-state-body">
        <code className="prp-error">{data.error}</code>
        {data.recorded && (
          <p>
            Last PR recorded here:{" "}
            <a href={data.recorded.url} target="_blank" rel="noreferrer">
              {data.recorded.number ? `#${data.recorded.number} ` : ""}
              {data.recorded.title || data.recorded.url}
            </a>
          </p>
        )}
      </div>
      <div className="prp-actions">{refresh}</div>
    </div>
  );
}

function PullRequestSummary({
  ticketId,
  pr,
  refresh,
}: {
  ticketId: string;
  pr: PullRequestStatus;
  refresh: ReactNode;
}) {
  const setCopilotOpen = useUiStore((s) => s.setCopilotOpen);
  const askToFix = useMutation({
    mutationFn: () => api.sendTriageMessage(ticketId, fixChecksPrompt(pr)),
    onMutate: () => setCopilotOpen(true),
    onError: (error) => toastActionFailed("Hand failing checks to triage chat", error),
  });
  const badge = stateBadge(pr);
  const summary = verdict(pr);
  const isOpen = pr.state === "open";
  const checks = [...pr.checks].sort((a, b) => OUTCOME_ORDER[a.outcome] - OUTCOME_ORDER[b.outcome]);
  const failing = countBy(checks, "failing");

  return (
    <div className="prp">
      <header className="prp-head">
        <div className="prp-meta">
          <span className={`prp-badge prp-badge--${badge.tone}`}>{badge.label}</span>
          <span className="prp-number">#{pr.number}</span>
          <span className="prp-branches" title={`${pr.head} into ${pr.base}`}>
            {pr.head} → {pr.base}
          </span>
        </div>
        <h3 className="prp-title">
          <a href={pr.url} target="_blank" rel="noreferrer">
            {pr.title}
          </a>
        </h3>
        <div className="prp-size">
          <span className="prp-add">+{pr.additions.toLocaleString()}</span>{" "}
          <span className="prp-del">−{pr.deletions.toLocaleString()}</span> · {pr.changed_files} file
          {pr.changed_files === 1 ? "" : "s"}
        </div>
      </header>

      <p className={`prp-verdict prp-verdict--${summary.tone}`} role="status">
        {summary.text}
      </p>

      <MergeAndCleanUpButton ticketId={ticketId} pr={pr} />

      <div className="prp-actions prp-actions--start">
        <a
          className={`${pr.mergeable_now ? "btn-secondary" : "btn-primary"} prp-link-btn`}
          href={pr.url}
          target="_blank"
          rel="noreferrer"
        >
          Open on GitHub
        </a>
        {isOpen && failing > 0 && (
          <Button variant="secondary" disabled={askToFix.isPending} onClick={() => askToFix.mutate()}>
            {askToFix.isPending ? "Sending…" : askToFix.isSuccess ? "Sent to triage chat" : "Ask triage to fix"}
          </Button>
        )}
        {refresh}
      </div>

      {isOpen && (
        <section className="prp-section" aria-labelledby="prp-checks-heading">
          <div className="prp-checks-head">
            <h4 id="prp-checks-heading" className="prp-section-title">
              Checks
            </h4>
            {checks.length > 0 && (
              <span className="prp-tally">
                {TALLY_ORDER.filter((outcome) => countBy(checks, outcome) > 0).map((outcome) => (
                  <span key={outcome} className={`prp-tally-item--${outcome}`}>
                    {countBy(checks, outcome)} {OUTCOME_LABEL[outcome].toLowerCase()}
                  </span>
                ))}
              </span>
            )}
          </div>
          {checks.length === 0 ? (
            <p className="prp-muted">No CI checks reported for this PR.</p>
          ) : (
            <ul className="prp-checks">
              {checks.map((check) => (
                <li key={`${check.name}-${check.url}`} className={`prp-check prp-check--${check.outcome}`}>
                  <span className="prp-check-icon" aria-hidden="true">
                    {OUTCOME_GLYPH[check.outcome]}
                  </span>
                  {check.url ? (
                    <a className="prp-check-name" href={check.url} target="_blank" rel="noreferrer">
                      {check.name}
                    </a>
                  ) : (
                    <span className="prp-check-name">{check.name}</span>
                  )}
                  <span className="prp-outcome">{OUTCOME_LABEL[check.outcome]}</span>
                </li>
              ))}
            </ul>
          )}
          {pr.unsigned_commits.length > 0 && (
            <div className="prp-unsigned">
              <h4 className="prp-section-title">Unsigned commits</h4>
              <ul className="prp-unsigned-list">
                {pr.unsigned_commits.map((line) => (
                  <li key={line}>
                    <code>{line}</code>
                  </li>
                ))}
              </ul>
              <p className="prp-muted">
                Re-sign them on the branch (<code>git rebase -r --exec &apos;git commit --amend --no-edit -S&apos;</code>{" "}
                from the oldest one&rsquo;s parent) and force-push, or squash the branch into one signed commit.
              </p>
            </div>
          )}
          <dl className="prp-facts">
            <div className="prp-fact">
              <dt>Review</dt>
              <dd>{REVIEW_LABEL[pr.review]}</dd>
            </div>
            <div className={`prp-fact${pr.has_conflicts ? " prp-fact--bad" : ""}`}>
              <dt>Conflicts</dt>
              <dd>{pr.has_conflicts ? `Yes, with ${pr.base}` : "None"}</dd>
            </div>
          </dl>
        </section>
      )}

      {pr.body.trim() && (
        <details className="prp-section prp-body" open>
          <summary className="prp-section-title">Description</summary>
          <MarkdownContent content={pr.body} normalize={false} readerTitle={`PR #${pr.number}`} />
        </details>
      )}
    </div>
  );
}

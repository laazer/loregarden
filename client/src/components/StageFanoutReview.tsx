/**
 * N attempts at one stage, side by side, and the one decision that ends them.
 *
 * The columns are the product: the attempts only mean something next to each
 * other, which is why this is a scrolling row of full diffs rather than a list
 * you click through one at a time. Every diff is against the same base, so the
 * columns are comparable line for line.
 *
 * The two buttons are deliberately asymmetric. Promoting names an attempt;
 * declining names none, and says so, because "none of these" is a real answer
 * and the alternative — leaving the group open — leaks N worktrees.
 */

import { useState } from "react";

import { describeError } from "../state/toastStore";
import { stageFanoutApi, type FanoutAttempt, type FanoutGroup } from "../lib/stageFanoutApi";
import "./StageFanoutReview.css";

interface Props {
  group: FanoutGroup;
  onSettled: (group: FanoutGroup) => void;
}

const SETTLED_OUTCOMES = new Set(["promoted", "declined", "cancelled", "failed"]);

export function StageFanoutReview({ group, onSettled }: Props) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const settled = SETTLED_OUTCOMES.has(group.outcome);

  const diffFor = (attempt: FanoutAttempt) =>
    group.diffs?.find((diff) => diff.attempt_id === attempt.id);

  async function settle(action: () => Promise<FanoutGroup>, key: string) {
    setBusy(key);
    setError("");
    try {
      onSettled(await action());
    } catch (err) {
      setError(describeError(err, "Could not settle this fan-out"));
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="fanout-review" aria-label={`Attempts at stage ${group.stage_key}`}>
      <header className="fanout-review__header">
        <div>
          <span className="fanout-review__title">
            {group.attempts.length} attempts at <code>{group.stage_key}</code>
          </span>
          <span className="fanout-review__hint">
            {settled
              ? `Settled — ${group.outcome}`
              : "Every attempt ran in its own worktree. Promoting one discards the rest."}
          </span>
        </div>
        {!settled && (
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={!!busy}
            onClick={() => settle(() => stageFanoutApi.decline(group.ticket_id, group.id), "decline")}
          >
            {busy === "decline" ? "Discarding…" : "Keep none"}
          </button>
        )}
      </header>

      {error && (
        <p className="fanout-review__error" role="alert">
          {error}
        </p>
      )}

      <div className="fanout-review__columns">
        {group.attempts.map((attempt) => {
          const diff = diffFor(attempt);
          const won = group.winner_attempt_id === attempt.id;
          return (
            <article
              key={attempt.id}
              className={`fanout-attempt ${won ? "is-winner" : ""}`.trim()}
              data-testid={`fanout-attempt-${attempt.attempt_index}`}
            >
              <header className="fanout-attempt__header">
                <span className="fanout-attempt__name">{attempt.attempt_name}</span>
                <span className={`fanout-attempt__status is-${attempt.status}`}>
                  {won ? "promoted" : attempt.status}
                </span>
              </header>
              <div className="fanout-attempt__meta">
                <code title={attempt.branch}>{attempt.branch || "no branch"}</code>
                {diff ? <span>{diff.files_changed} file(s)</span> : null}
              </div>

              {attempt.failure_details ? (
                <p className="fanout-attempt__failure">{attempt.failure_details}</p>
              ) : null}

              <pre className="fanout-attempt__diff">
                {diff?.patch || "No changes recorded for this attempt."}
              </pre>
              {diff?.truncated ? (
                <p className="fanout-attempt__truncated">
                  Diff truncated — check out {attempt.branch} to read the rest.
                </p>
              ) : null}

              {!settled && (
                <button
                  type="button"
                  className="btn-primary btn-compact fanout-attempt__promote"
                  disabled={!!busy || !attempt.branch}
                  onClick={() =>
                    settle(
                      () => stageFanoutApi.promote(group.ticket_id, group.id, attempt.id),
                      attempt.id,
                    )
                  }
                >
                  {busy === attempt.id ? "Promoting…" : "Promote this one"}
                </button>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

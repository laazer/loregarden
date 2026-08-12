/**
 * N attempts at one stage, side by side, and the one decision that ends them.
 *
 * The columns are the product: the attempts only mean something next to each
 * other, which is why this is a row of manifests rather than a list you click
 * through one at a time. Every manifest is measured against the same base, so
 * the numbers are comparable.
 *
 * Patches load a file at a time. Opening the comparison would otherwise ship
 * every attempt's entire output before anyone has decided which one they care
 * about — and a fan-out over an implement stage is where those are largest.
 *
 * The two buttons are deliberately asymmetric. Promoting names an attempt;
 * declining names none, and says so, because "none of these" is a real answer
 * and the alternative — leaving the group open — leaks N worktrees.
 */

import { useState } from "react";

import { describeError } from "../state/toastStore";
import {
  stageFanoutApi,
  type FanoutAttempt,
  type FanoutDiff,
  type FanoutGroup,
} from "../lib/stageFanoutApi";
import "./StageFanoutReview.css";

interface Props {
  group: FanoutGroup;
  onSettled: (group: FanoutGroup) => void;
}

const SETTLED_OUTCOMES = new Set(["promoted", "declined", "cancelled", "failed"]);

/** Only a finished, successful attempt is a candidate to keep. */
const PROMOTABLE = new Set(["succeeded"]);

/** Statuses that mean the attempt is still working. */
const IN_FLIGHT = new Set(["planned", "queued", "running", "awaiting_permission"]);

export function StageFanoutReview({ group, onSettled }: Props) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [openFile, setOpenFile] = useState<Record<string, string>>({});
  const [patches, setPatches] = useState<Record<string, string>>({});

  const settled = SETTLED_OUTCOMES.has(group.outcome);
  const anyInFlight = group.attempts.some((attempt) => IN_FLIGHT.has(attempt.status));

  const diffFor = (attempt: FanoutAttempt): FanoutDiff | undefined =>
    group.diffs?.find((diff) => diff.attempt_id === attempt.id);

  async function showFile(attempt: FanoutAttempt, path: string) {
    const key = `${attempt.id}:${path}`;
    setOpenFile((current) => ({ ...current, [attempt.id]: path }));
    if (patches[key] !== undefined) return;
    setPatches((current) => ({ ...current, [key]: "Loading…" }));
    try {
      const result = await stageFanoutApi.fileDiff(group.ticket_id, group.id, attempt.id, path);
      setPatches((current) => ({ ...current, [key]: result.patch || "(no changes)" }));
    } catch (err) {
      setPatches((current) => ({
        ...current,
        [key]: describeError(err, "Could not load this file's diff"),
      }));
    }
  }

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
              : anyInFlight
                ? "Still running. Nothing can be promoted until every attempt is in."
                : "Every attempt ran in its own worktree. Promoting one discards the rest."}
          </span>
        </div>
        {!settled && (
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={!!busy || anyInFlight}
            onClick={() =>
              settle(() => stageFanoutApi.decline(group.ticket_id, group.id), "decline")
            }
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
          const shown = openFile[attempt.id];
          const status = won ? "promoted" : attempt.status;
          return (
            <article
              key={attempt.id}
              className={`fanout-attempt ${won ? "is-winner" : ""}`.trim()}
              data-testid={`fanout-attempt-${attempt.attempt_index}`}
            >
              <header className="fanout-attempt__header">
                <span className="fanout-attempt__name">{attempt.attempt_name}</span>
                <span className={`fanout-attempt__status is-${status}`}>{status}</span>
              </header>
              <div className="fanout-attempt__meta">
                <code title={attempt.branch}>{attempt.branch || "no branch"}</code>
                {diff ? (
                  <span>
                    {diff.files_changed} file(s){" "}
                    <span className="fanout-attempt__added">+{diff.additions}</span>{" "}
                    <span className="fanout-attempt__removed">−{diff.deletions}</span>
                  </span>
                ) : null}
              </div>

              {attempt.failure_details ? (
                <p className="fanout-attempt__failure">{attempt.failure_details}</p>
              ) : null}

              <ul className="fanout-attempt__files">
                {(diff?.files ?? []).map((file) => (
                  <li key={file.path}>
                    <button
                      type="button"
                      className={`fanout-attempt__file ${
                        shown === file.path ? "is-open" : ""
                      }`.trim()}
                      onClick={() => showFile(attempt, file.path)}
                    >
                      <span className="fanout-attempt__file-path">{file.path}</span>
                      <span className="fanout-attempt__added">+{file.additions}</span>
                      <span className="fanout-attempt__removed">−{file.deletions}</span>
                    </button>
                  </li>
                ))}
                {(diff?.files ?? []).length === 0 && (
                  <li className="fanout-attempt__empty">No changes recorded for this attempt.</li>
                )}
              </ul>

              {shown ? (
                <pre className="fanout-attempt__diff" data-testid={`patch-${attempt.attempt_index}`}>
                  {patches[`${attempt.id}:${shown}`] ?? ""}
                </pre>
              ) : null}

              {!settled && (
                <button
                  type="button"
                  className="btn-primary btn-compact fanout-attempt__promote"
                  disabled={!!busy || !PROMOTABLE.has(attempt.status) || !attempt.branch}
                  title={
                    PROMOTABLE.has(attempt.status)
                      ? "Merge this attempt and discard the others"
                      : `An attempt that is ${attempt.status} cannot be promoted`
                  }
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

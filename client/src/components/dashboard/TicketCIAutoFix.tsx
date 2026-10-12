import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import type { AutoFixAttempt, AutoFixStatusValue, CIRunResult } from "../../api/types";
import { formatLocalTimestamp, formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";

/** An attempt in flight settles when its fix turn does: minutes, not seconds. */
const IN_FLIGHT_POLL_MS = 30_000;

const IN_FLIGHT: ReadonlySet<AutoFixStatusValue> = new Set(["pending", "running"]);
const ATTEMPT_LABEL: Record<AutoFixStatusValue, string> = {
  pending: "Starting",
  running: "Running",
  succeeded: "Fixed",
  failed: "Failed",
  refused: "Refused",
};
/** Decorative: the label is spelled out beside it. */
const ATTEMPT_GLYPH: Record<AutoFixStatusValue, string> = {
  pending: "•",
  running: "•",
  succeeded: "✓",
  failed: "✕",
  refused: "!",
};

const ciStatusKey = (ticketId: string) => ["ci-status", ticketId] as const;

function shortSha(result: CIRunResult): string {
  return result.head_sha.slice(0, 12);
}

/**
 * "Has Loregarden tried to fix this PR's CI, and if it stopped, why?"
 *
 * The CI loop (`docs/ci-setup.md`) reopens a done ticket for a fix turn when its
 * PR's checks fail, and records each try. A refused attempt is one a person
 * must act on, so its reason is shown in full. Retry asks the server for
 * another turn now; a refusal comes back as a 409 naming why, and is shown
 * here because a manual refusal is never written to the history.
 */
export function TicketCIAutoFix({ ticketId, isOpen }: { ticketId: string; isOpen: boolean }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ciStatusKey(ticketId),
    queryFn: () => api.ciStatus(ticketId),
    staleTime: 15_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      const busy =
        data?.ci_status?.status === "pending" || data?.auto_fix_history.some((attempt) => IN_FLIGHT.has(attempt.status));
      return busy ? IN_FLIGHT_POLL_MS : false;
    },
  });
  const settled = () => {
    void qc.invalidateQueries({ queryKey: ciStatusKey(ticketId) });
    void qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
  };
  const retry = useMutation({ mutationFn: () => api.triggerAutoFix(ticketId), onSuccess: settled });
  const skip = useMutation({ mutationFn: () => api.skipCICheck(ticketId), onSuccess: settled });

  // A local database read: under the ~200ms where a skeleton would only flash.
  if (status.isPending) return null;
  if (status.isError) {
    return (
      <section className="prp-section" aria-labelledby="prp-autofix-heading" role="alert">
        <h4 id="prp-autofix-heading" className="prp-section-title">
          Auto-fix
        </h4>
        <p className="prp-muted">
          Could not load this ticket&rsquo;s CI fix attempts: {describeError(status.error, "the request failed")}
        </p>
        <div className="prp-actions prp-actions--start">
          <Button variant="secondary" compact disabled={status.isFetching} onClick={() => void status.refetch()}>
            {status.isFetching ? "Checking…" : "Try again"}
          </Button>
        </div>
      </section>
    );
  }

  const { ci_status: result, auto_fix_history: history } = status.data;
  if (!isOpen && history.length === 0) return null;
  const attempts = [...history].reverse();
  const inFlight = history.some((attempt) => IN_FLIGHT.has(attempt.status));
  const failing = isOpen && (result?.status === "failing" || result?.status === "partial");
  const busy = retry.isPending || skip.isPending;
  const actionError = retry.error ?? skip.error;

  return (
    <section className="prp-section" aria-labelledby="prp-autofix-heading">
      <div className="prp-checks-head">
        <h4 id="prp-autofix-heading" className="prp-section-title">
          Auto-fix
        </h4>
        {history.length > 0 && (
          <span className="prp-tally">
            {history.length} attempt{history.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {!result && history.length === 0 && (
        <p className="prp-muted">
          Loregarden has not recorded CI for this PR yet. It reads the PR&rsquo;s checks every few minutes, and when
          they fail it reopens the ticket for a fix turn.
        </p>
      )}
      {result && history.length === 0 && (
        <p className="prp-muted">
          No fix attempts. CI is {result.status} at <code className="prp-branch">{shortSha(result)}</code>.
        </p>
      )}
      {attempts.length > 0 && (
        <ol className="prp-checks" aria-label="Fix attempts, newest first">
          {attempts.map((attempt) => (
            <AttemptRow key={attempt.id} attempt={attempt} />
          ))}
        </ol>
      )}

      {failing && (
        <div className="prp-actions prp-actions--start">
          <Button
            variant="secondary"
            disabled={busy || inFlight}
            title={inFlight ? "A fix attempt is already running" : undefined}
            onClick={() => retry.mutate()}
          >
            {retry.isPending ? "Starting…" : "Retry auto-fix"}
          </Button>
          <SkipCIGate result={result} busy={busy} onSkip={() => skip.mutate()} />
        </div>
      )}
      {actionError && (
        <p className="prp-error" role="alert">
          {describeError(actionError, retry.error ? "Auto-fix did not start" : "Could not skip the CI gate")}
        </p>
      )}
    </section>
  );
}

function AttemptRow({ attempt }: { attempt: AutoFixAttempt }) {
  const when = attempt.completed_at ?? attempt.created_at;
  return (
    <li className={`prp-check prp-attempt prp-attempt--${attempt.status}`}>
      <span className="prp-check-icon" aria-hidden="true">
        {ATTEMPT_GLYPH[attempt.status]}
      </span>
      <div className="prp-attempt-body">
        <span className="prp-check-name">
          Attempt {attempt.attempt_number}: {ATTEMPT_LABEL[attempt.status]}
        </span>
        {attempt.result_summary && <span className="prp-attempt-reason">{attempt.result_summary}</span>}
        <time className="prp-attempt-when" dateTime={when} title={formatLocalTimestamp(when)}>
          {formatRelativeAge(when)}
        </time>
      </div>
    </li>
  );
}

/** Skipping waives CI for one commit, so the first click asks. */
function SkipCIGate({ result, busy, onSkip }: { result: CIRunResult | null; busy: boolean; onSkip: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  if (!result) return null;
  if (!confirming) {
    return (
      <Button variant="secondary" disabled={busy} onClick={() => setConfirming(true)}>
        Skip CI gate…
      </Button>
    );
  }
  return (
    <div
      className="prp-merge-confirm"
      role="group"
      aria-label="Confirm skipping the CI gate"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) setConfirming(false);
      }}
    >
      <p className="prp-merge-question">
        Stop waiting on CI for <code>{shortSha(result)}</code>? Approval no longer blocks on its failing checks. The
        next commit is checked again.
      </p>
      <div className="prp-actions prp-actions--start">
        <Button
          ref={confirmRef}
          variant="primary"
          disabled={busy}
          onClick={() => {
            onSkip();
            setConfirming(false);
          }}
        >
          {busy ? "Skipping…" : "Skip CI gate"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={() => setConfirming(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

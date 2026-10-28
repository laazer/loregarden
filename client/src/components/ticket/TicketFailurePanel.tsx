import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "../../api/client";
import type { TicketDetail } from "../../api/client";
import { duration } from "../../lib/duration";
import { isErrorLine, ticketFailure } from "../../lib/ticketFailure";
import { formatShortTime } from "../../lib/ticketOutputs";
import { buildTicketTimeline } from "../../lib/ticketTimeline";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { blockKindLabel, blockKindMeaning } from "../../utils/blockKinds";
import { CopyValueButton } from "../CopyValueButton";
import { Button } from "../ui/Button";

import "./TicketFailurePanel.css";

/** The run fields the panel reads: enough to resolve an error's run code to a log. */
export interface FailureRun {
  id: string;
  run_code: string;
}

/**
 * "What stopped this ticket?" — answered before any history, at the top of the
 * ticket view: the stage, which attempt, and the run's own output with its
 * error lines marked. Renders nothing while nothing has stopped the ticket.
 */
export function TicketFailurePanel({
  ticket,
  runs,
  isActive,
  onOpenRunLog,
}: {
  ticket: TicketDetail;
  runs: readonly FailureRun[];
  isActive: boolean;
  onOpenRunLog: (runId: string) => void;
}) {
  // Same key and cadence as the Timeline's, so the two share one fetch.
  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticket.id],
    queryFn: () => api.ticketLedger(ticket.id),
    refetchInterval: isActive ? 2000 : false,
  });
  const visits = useMemo(() => buildTicketTimeline(ledger.data?.visits ?? [], []).visits, [ledger.data?.visits]);
  const failure = ticketFailure(ticket, visits);
  // Held for the ledger (a short read the Timeline shares) so the heading does
  // not change from "Blocked" to the stage under the reader. A failed ledger
  // read still shows the ticket's own message; the Timeline names that failure.
  if (!failure || ledger.isPending) return null;

  const { blocked, message, error, visit, attempt, attemptNumber } = failure;
  const stageKey = visit?.visit.stage_key ?? error?.stage_key ?? "";
  const stageName = ticket.stages.find((stage) => stage.key === stageKey)?.name ?? stageKey;
  const runCode = attempt?.run_code ?? error?.run_code ?? "";
  // The error artifact records a run_code; the log fetch needs the run's id.
  const runId = attempt?.run_id ?? (runCode ? runs.find((run) => run.run_code === runCode)?.id : undefined);
  // The run's own stderr says it best; the ticket's message stands in when the
  // run recorded none (a landing refused, a usage limit reported upstream).
  const output = attempt?.error_tail || message;
  const lines = output ? output.split("\n") : [];
  const agent = attempt?.agent_id ?? error?.agent_id ?? "";

  const meta = [
    attempt && visit ? `Attempt ${attemptNumber} of ${visit.visit.attempts.length}` : "",
    agent,
    attempt?.started_at ? formatShortTime(attempt.started_at) : "",
    attempt?.duration_seconds != null ? duration(attempt.duration_seconds) : "",
  ].filter(Boolean);

  return (
    <section
      className={`tf ${blocked ? "tf--bad" : "tf--warn"}`}
      aria-label={blocked ? "Blocking issue" : "Last run failed"}
    >
      <div className="tf-head">
        <span className="tf-mark" aria-hidden>
          !
        </span>
        <div className="tf-heading">
          <h2 className="tf-title">{stageName || (blocked ? "Blocked" : "Last run failed")}</h2>
          {meta.length > 0 ? (
            <div className="tf-meta" title={attempt?.started_at ? formatLocalTimestamp(attempt.started_at) : undefined}>
              {meta.join(" · ")}
            </div>
          ) : null}
        </div>
        <span className="tf-chip">
          {blocked ? `Blocked${ticket.block_kind ? ` · ${blockKindLabel(ticket.block_kind)}` : ""}` : "Last run failed"}
        </span>
      </div>

      {blocked && ticket.block_kind ? <p className="tf-sub">{blockKindMeaning(ticket.block_kind)}</p> : null}

      {lines.length > 0 ? (
        <div className="tf-out">
          <div className="tf-out-head">
            <span className="tf-out-title">{attempt?.error_tail ? "Agent run output" : "What stopped it"}</span>
            {runCode ? <span className="tf-out-run">{runCode}</span> : null}
            <span className="tf-out-actions">
              <CopyValueButton value={output} what="the failure output" className="tf-out-action">
                Copy
              </CopyValueButton>
              {runId ? (
                <Button variant="plain" className="tf-out-action" onClick={() => onOpenRunLog(runId)}>
                  Full log
                </Button>
              ) : null}
            </span>
          </div>
          {/* Scrolls when a long message overflows it, so it takes focus for the keys. */}
          <ol className="tf-lines" tabIndex={0} aria-label="Failure output">
            {lines.map((line, index) => (
              <li key={index} className={isErrorLine(line) ? "is-error" : undefined}>
                {line || " "}
              </li>
            ))}
          </ol>
        </div>
      ) : ticket.block_kind === "tickets" ? null : (
        <p className="tf-sub">
          {ticket.child_count > 0
            ? "Blocked because work under it is blocked. Open its child tickets to see which."
            : "No reason was recorded on this ticket. The run log of the stage that stopped is the place to look."}
        </p>
      )}
    </section>
  );
}

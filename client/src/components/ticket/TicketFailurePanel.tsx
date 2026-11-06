import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { TicketDetail } from "../../api/client";
import { duration } from "../../lib/duration";
import { isErrorLine, ticketFailure } from "../../lib/ticketFailure";
import { formatShortTime } from "../../lib/ticketOutputs";
import { buildTicketTimeline } from "../../lib/ticketTimeline";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { agentFix, blockHeadline } from "../../utils/blockKinds";
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
  const [outputOpen, setOutputOpen] = useState(true);
  const outputId = useId();
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
  const headline = blockHeadline(ticket);
  // An agent's fix is amber like any wait: red is for what needs someone.
  const tone = blocked && !agentFix(ticket) ? "tf--bad" : "tf--warn";
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
      className={`tf ${tone}`}
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
          {blocked ? headline.label : "Last run failed"}
        </span>
      </div>

      {blocked && headline.meaning ? <p className="tf-sub">{headline.meaning}</p> : null}

      {lines.length > 0 ? (
        <div className={outputOpen ? "tf-out" : "tf-out is-collapsed"}>
          <div className="tf-out-head">
            <Button
              variant="plain"
              className="tf-out-toggle"
              aria-expanded={outputOpen}
              aria-controls={outputId}
              onClick={() => setOutputOpen((open) => !open)}
            >
              <span className="tf-out-chevron" aria-hidden>
                ▸
              </span>
              <span className="tf-out-title">{attempt?.error_tail ? "Agent run output" : "What stopped it"}</span>
            </Button>
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
          <ol id={outputId} className="tf-lines" tabIndex={0} aria-label="Failure output" hidden={!outputOpen}>
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

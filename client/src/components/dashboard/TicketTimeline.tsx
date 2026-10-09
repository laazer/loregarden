import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { TicketDetail } from "../../api/client";
import type { LedgerAttempt, TicketArtifactItem, TransientRetryNotice } from "../../api/types";
import { duration } from "../../lib/duration";
import { ACTIVE_LEDGER_STATUSES } from "../../lib/ledgerStatus";
import { buildTicketTimeline, isErrorOutput, isFailedStatus, type TimelineVisit } from "../../lib/ticketTimeline";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { navigateToTicketTab } from "../../lib/useAppNavigation";
import { describeError } from "../../state/toastStore";
import { blockKindLabel, blockKindMeaning } from "../../utils/blockKinds";
import { LaneLogView } from "../logs/LaneLogView";
import { StageFanoutPanel } from "../StageFanoutPanel";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { formatShortTime, openOutput, outputTitle } from "../../lib/ticketOutputs";
import { KindTag, VerdictPill } from "./outputParts";
import "./ArtifactPane.css";

/** The run fields the timeline reads from the ticket's run list. */
export interface TimelineRun {
  id: string;
  run_code: string;
  status: string;
  stderr?: string;
}

const STDERR_PREVIEW_LINES = 8;

/**
 * "What happened to this ticket, and where is it now?"
 *
 * One list of stage visits, oldest first, each holding its attempts and the
 * outputs that landed while it ran. It replaces four views that each held a
 * slice of the same story — Logs, Context, Ledger and Errors — so an error is
 * read next to the stage it happened in, and a revisit shows up as a second
 * visit rather than as one more row in a flat list.
 *
 * What needs a person comes first and only when there is something: a block,
 * automatic retries, approvals waiting.
 */
export function TicketTimeline({
  ticket,
  runs,
  isActive,
  pendingApprovals,
  onOpenRunLog,
}: {
  ticket: TicketDetail;
  runs: readonly TimelineRun[];
  isActive: boolean;
  pendingApprovals: number;
  onOpenRunLog: (runId: string) => void;
}) {
  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticket.id],
    queryFn: () => api.ticketLedger(ticket.id),
    refetchInterval: isActive ? 2000 : false,
  });
  // Same key as the Dashboard's feed query, so this reuses that fetch.
  const feed = useQuery({
    queryKey: ["ticket-artifacts", ticket.id],
    queryFn: () => api.ticketArtifacts(ticket.id),
    refetchInterval: isActive ? 2000 : false,
  });

  const timeline = useMemo(
    () => buildTicketTimeline(ledger.data?.visits ?? [], feed.data?.items ?? []),
    [ledger.data?.visits, feed.data?.items],
  );

  if (ledger.isPending || feed.isPending) {
    return <PaneSkeleton variant="list" rows={6} label="Loading timeline…" />;
  }
  if (ledger.isError || feed.isError) {
    const failed = ledger.isError ? "run ledger" : "artifact feed";
    return (
      <div className="ap-state" role="alert">
        <div className="ap-state-title">Could not load this ticket&rsquo;s {failed}</div>
        <div className="ap-state-body">{describeError(ledger.error ?? feed.error, "The request failed.")}</div>
        <Button variant="secondary" compact onClick={() => void (ledger.isError ? ledger.refetch() : feed.refetch())}>
          Try again
        </Button>
      </div>
    );
  }

  const stageNames = new Map(ticket.stages.map((stage) => [stage.key, stage.name]));
  const nothingYet = timeline.visits.length === 0 && timeline.beforeFirstRun.length === 0;
  const current = [...timeline.visits].reverse().find((visit) => visit.active);
  const data = ledger.data;

  return (
    <div className="tl">
      <Attention ticket={ticket} runs={runs} pendingApprovals={pendingApprovals} onOpenRunLog={onOpenRunLog} />

      {nothingYet ? (
        <div className="ap-state">
          <div className="ap-state-title">Nothing has run for this ticket yet</div>
          <div className="ap-state-body">
            Start a run from the workflow pane. Each stage appears here as it runs, with what it produced.
          </div>
        </div>
      ) : (
        <>
          <div className="tl-summary">
            {current ? (
              <span className="tl-summary-now">
                Now: {stageNames.get(current.visit.stage_key) ?? current.visit.stage_key}
              </span>
            ) : null}
            <span>
              {data.total_runs} run{data.total_runs === 1 ? "" : "s"}
              {data.total_seconds > 0 ? ` · ${duration(data.total_seconds)} of agent time` : ""}
            </span>
            {data.reworked_stages.length > 0 ? (
              <span className="tl-summary-warn">Went back to: {data.reworked_stages.join(", ")}</span>
            ) : null}
          </div>

          <ol className="tl-list">
            {timeline.beforeFirstRun.length > 0 ? (
              <li className="tl-visit tl-visit--quiet">
                <div className="tl-visit-static">
                  <span className="tl-dot" aria-hidden />
                  <span className="tl-stage">Before the first run</span>
                  <span className="tl-meta">
                    {timeline.beforeFirstRun.length} output{timeline.beforeFirstRun.length === 1 ? "" : "s"}
                  </span>
                </div>
                <OutputList outputs={timeline.beforeFirstRun} />
              </li>
            ) : null}
            {timeline.visits.map((visit, index) => (
              <VisitItem
                key={visit.key}
                visit={visit}
                stageName={stageNames.get(visit.visit.stage_key) ?? visit.visit.stage_key}
                isLatest={index === timeline.visits.length - 1}
                runs={runs}
                onOpenRunLog={onOpenRunLog}
              />
            ))}
          </ol>
        </>
      )}

      {ticket.stages.length > 0 ? (
        <details className="tl-fanout">
          <summary>Run one stage several ways</summary>
          <StageFanoutPanel
            ticketId={ticket.id}
            stages={ticket.stages.map((stage) => ({ key: stage.key, name: stage.name }))}
          />
        </details>
      ) : null}
    </div>
  );
}

// An error output on a stage that succeeded is a warning about the stage, not a
// failure of it: on lg-initiatives-cross-755 every stage carried the same
// "handoff not validated" row, and painting them all as failed hid the one
// signal that matters — a run that actually failed.
function visitTone(visit: TimelineVisit): string {
  if (visit.active) return "active";
  if (isFailedStatus(visit.visit.status) || visit.failedAttempts.length > 0) return "bad";
  if (visit.visit.visit_number > 1 || visit.errorCount > 0) return "warn";
  return "good";
}

function attemptsLabel(visit: TimelineVisit): string {
  const count = visit.visit.attempts.length;
  if (visit.visit.is_parallel) return `${count} lanes`;
  return count > 1 ? `${count} attempts` : "";
}

function ordinal(n: number): string {
  const suffix = n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th";
  return `${n}${suffix} pass`;
}

function VisitItem({
  visit,
  stageName,
  isLatest,
  runs,
  onOpenRunLog,
}: {
  visit: TimelineVisit;
  stageName: string;
  isLatest: boolean;
  runs: readonly TimelineRun[];
  onOpenRunLog: (runId: string) => void;
}) {
  const bodyId = useId();
  const tone = visitTone(visit);
  // Open by default where there is something to act on: what is running now,
  // what failed, and the most recent stage. Everything else is history.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? (isLatest || visit.active || tone === "bad");
  const failedRuns = visit.failedAttempts.length;
  const extra = attemptsLabel(visit);
  // One live log per stage: a five-lane fan-out tailing five logs at once is
  // five polls and a wall of interleaved output. The other lanes keep "Log".
  const liveRunId = visit.visit.attempts.find((attempt) => ACTIVE_LEDGER_STATUSES.has(attempt.status))?.run_id;

  return (
    <li className={`tl-visit tl-visit--${tone}`}>
      <Button
        variant="plain"
        className="tl-visit-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setToggled(!open)}
      >
        <span className="tl-dot" aria-hidden />
        <span className="tl-stage">{stageName}</span>
        {visit.visit.visit_number > 1 ? <span className="tl-tag tl-tag--warn">{ordinal(visit.visit.visit_number)}</span> : null}
        {extra ? <span className="tl-meta">{extra}</span> : null}
        {failedRuns > 0 ? <span className="tl-tag tl-tag--bad">{failedRuns} failed</span> : null}
        {visit.errorCount > 0 ? (
          <span className="tl-tag tl-tag--warn">
            {visit.errorCount} error{visit.errorCount === 1 ? "" : "s"}
          </span>
        ) : null}
        {visit.outputs.length > 0 ? (
          <span className="tl-meta">
            {visit.outputs.length} output{visit.outputs.length === 1 ? "" : "s"}
          </span>
        ) : null}
        <span className="tl-when">
          {visit.active ? (
            <span className="tl-live">{visit.visit.status.replace("_", " ")}</span>
          ) : (
            duration(visit.durationSeconds)
          )}
          {visit.startedAt ? (
            <span title={formatLocalTimestamp(visit.startedAt.toISOString())}>
              {" · "}
              {formatShortTime(visit.startedAt.toISOString())}
            </span>
          ) : null}
        </span>
        <span className="tl-chevron" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </Button>

      {open ? (
        <div id={bodyId} className="tl-visit-body">
          <ul className="tl-attempts">
            {visit.visit.attempts.map((attempt) => (
              <AttemptRow
                key={attempt.run_id}
                attempt={attempt}
                stderr={runs.find((run) => run.id === attempt.run_id)?.stderr}
                showLiveLog={attempt.run_id === liveRunId}
                onOpenRunLog={onOpenRunLog}
              />
            ))}
          </ul>
          {visit.outputs.length > 0 ? (
            <OutputList outputs={visit.outputs} stageName={stageName} />
          ) : (
            <div className="tl-none">No outputs recorded while this stage ran.</div>
          )}
        </div>
      ) : null}
    </li>
  );
}

function AttemptRow({
  attempt,
  stderr,
  showLiveLog,
  onOpenRunLog,
}: {
  attempt: LedgerAttempt;
  stderr?: string;
  showLiveLog: boolean;
  onOpenRunLog: (runId: string) => void;
}) {
  const running = ACTIVE_LEDGER_STATUSES.has(attempt.status);
  const failed = isFailedStatus(attempt.status);
  const excerpt = failed && stderr ? stderr.trim().split("\n").slice(-STDERR_PREVIEW_LINES).join("\n") : "";
  return (
    <li>
      <div className="tl-attempt-row">
        <span className="tl-agent">
          {attempt.agent_id}
          {attempt.skill_name ? <span className="tl-skill"> · {attempt.skill_name}</span> : null}
        </span>
        <span className={`tl-status${running ? " tl-status--active" : failed ? " tl-status--bad" : ""}`}>
          {attempt.status.replace("_", " ")}
        </span>
        <span className="tl-attempt-time" title={formatLocalTimestamp(attempt.started_at)}>
          {attempt.duration_seconds !== null ? duration(attempt.duration_seconds) : ""}
        </span>
        <Button
          variant="plain"
          className="tl-link"
          onClick={() => onOpenRunLog(attempt.run_id)}
          aria-label={`Open the log of ${attempt.agent_id} run ${attempt.run_code}`}
        >
          Log
        </Button>
      </div>
      {excerpt ? <pre className="tl-stderr">{excerpt}</pre> : null}
      {running && showLiveLog ? (
        <div className="tl-live-log">
          <LaneLogView runId={attempt.run_id} />
        </div>
      ) : null}
    </li>
  );
}

function OutputList({ outputs, stageName }: { outputs: readonly TicketArtifactItem[]; stageName?: string }) {
  return (
    <ul className="tl-outputs">
      {outputs.map((item) => (
        <li key={item.id}>
          <Button
            variant="plain"
            className={`tl-output${isErrorOutput(item) ? " tl-output--error" : ""}`}
            onClick={() => openOutput(item, stageName)}
          >
            <KindTag item={item} />
            <span className="tl-output-title">{outputTitle(item)}</span>
            <VerdictPill item={item} />
          </Button>
        </li>
      ))}
    </ul>
  );
}

/** Everything that wants a person, before any history. Renders nothing when nothing does. */
function Attention({
  ticket,
  runs,
  pendingApprovals,
  onOpenRunLog,
}: {
  ticket: TicketDetail;
  runs: readonly TimelineRun[];
  pendingApprovals: number;
  onOpenRunLog: (runId: string) => void;
}) {
  // The server sends only an error nothing has run since; older ones are
  // outputs on the visits below, not a state the ticket is in.
  const errorArt = ticket.artifacts?.error ?? null;
  const retries = ticket.artifacts?.transient_retries ?? [];
  // "Blocked" is the ticket's word for itself, never inferred from an artifact:
  // a failed run the orchestrator is about to retry is not a block.
  const blocked = ticket.state === "blocked" || Boolean(ticket.blocking_issues?.trim());
  const blockMessage = blocked ? ticket.blocking_issues?.trim() || errorArt?.message || "" : errorArt?.message || "";
  // The error artifact records a run_code; the log fetch needs the run's id.
  const errorRunId = errorArt ? runs.find((run) => run.run_code === errorArt.run_code)?.id : undefined;

  if (!blockMessage && !blocked && retries.length === 0 && pendingApprovals === 0) return null;

  return (
    <div className="tl-attention">
      {blocked || blockMessage ? (
        <section
          className={`tl-callout ${blocked ? "tl-callout--bad" : "tl-callout--warn"}`}
          aria-label={blocked ? "Blocking issue" : "Last run failed"}
        >
          <div className="tl-callout-head">
            <span className="tl-callout-title">
              {blocked ? `Blocked${ticket.block_kind ? ` · ${blockKindLabel(ticket.block_kind)}` : ""}` : "Last run failed"}
            </span>
            {errorArt ? (
              <span className="tl-callout-meta">
                {errorArt.stage_key} · {errorArt.agent_id} · {errorArt.run_code}
              </span>
            ) : null}
            {errorRunId ? (
              <Button variant="secondary" compact onClick={() => onOpenRunLog(errorRunId)}>
                View log
              </Button>
            ) : null}
          </div>
          {blocked && ticket.block_kind ? <div className="tl-callout-sub">{blockKindMeaning(ticket.block_kind)}</div> : null}
          {blockMessage ? (
            <pre className="tl-callout-body">{blockMessage}</pre>
          ) : (
            <div className="tl-callout-sub">
              {ticket.child_count > 0
                ? "Blocked because work under it is blocked. Open its child tickets to see which."
                : "No reason was recorded on this ticket. The run log of the stage that stopped is the place to look."}
            </div>
          )}
        </section>
      ) : null}
      {retries.length > 0 ? <RetryCallout notices={retries} /> : null}
      {pendingApprovals > 0 ? (
        <section className="tl-callout tl-callout--warn" aria-label="Approvals waiting">
          <div className="tl-callout-head">
            <span className="tl-callout-title">
              {pendingApprovals} approval{pendingApprovals === 1 ? "" : "s"} waiting on you
            </span>
            <Button variant="secondary" compact onClick={() => navigateToTicketTab(ticket.id, "approvals")}>
              Review
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}

/**
 * Stages the control plane re-ran by itself after an infrastructure failure.
 * Not an error — the stage was re-armed, not blocked — but without this a stage
 * that quietly ran five times looks exactly like one that ran once. Grouped by
 * stage: five retries of `implement` is one fact, not five.
 */
function RetryCallout({ notices }: { notices: readonly TransientRetryNotice[] }) {
  const byStage = new Map<string, TransientRetryNotice[]>();
  for (const notice of notices) {
    const bucket = byStage.get(notice.stage_key);
    if (bucket) bucket.push(notice);
    else byStage.set(notice.stage_key, [notice]);
  }
  return (
    <section className="tl-callout tl-callout--warn" aria-label="Automatic retries">
      <div className="tl-callout-head">
        <span className="tl-callout-title">Automatic retries</span>
      </div>
      {[...byStage.entries()].map(([stageKey, forStage]) => {
        const latest = forStage[forStage.length - 1];
        return (
          <div key={stageKey} className="tl-retry">
            <div className="tl-callout-meta">
              {stageKey} · {forStage.length}
              {forStage.length === 1 ? " retry" : " retries"}
              {latest.at ? ` · last ${formatLocalTimestamp(latest.at)}` : ""}
            </div>
            <div className="tl-callout-sub">{latest.message || "Re-dispatched after an infrastructure failure."}</div>
          </div>
        );
      })}
    </section>
  );
}

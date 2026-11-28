import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LogLine } from "../../../api/runLogTypes";
import {
  FEED_ROWS,
  agentDisplayName,
  buildAgentMap,
  selectMapAttempts,
} from "../../../lib/hive/agentMap";
import { buildToolActs, type ToolAct, type ToolActsModel } from "../../../lib/hive/toolActs";
import { ACTIVE_LEDGER_STATUSES } from "../../../lib/ledgerStatus";
import { RunLogModal } from "../../RunLogModal";
import { Button } from "../../ui/Button";
import { PaneSkeleton } from "../../ui/PaneSkeleton";
import "./HiveAgentMap.css";
import { HiveMapPicture } from "./HiveMapPicture";
import {
  STATE_MARK,
  STATUS_NOW_LINE,
  inWords,
  initials,
  isRingPlace,
  placeLabel,
  unknownActivity,
} from "./hiveMapText";

const POLL_MS = 2000;
const EMPTY_LINES: LogLine[] = [];
/** How long one pulse takes to cross a spoke; matches `hive-map-pulse` in the CSS. */
const PULSE_MS = 900;
/** The tail the server keeps, which is what a collapsed act's count is a count of. */
const LOG_TAIL_LINES = 1000;

/**
 * Hive's map view: every agent run on this ticket, what it is doing now, and whether any of
 * its tool calls failed.
 *
 * It answers one question — "what is each agent run on this ticket doing right now, and did
 * any of its tool calls fail?" — and the two side lists are what answer it. The SVG beside
 * them says nothing absent from them.
 *
 * The act model (`lib/hive/toolActs.ts`) decides what one run is doing; `buildAgentMap` counts
 * the places for the picture. Both read the same log lines through the same classifier.
 */
export function HiveAgentMap({ ticket }: { ticket: TicketDetail }) {
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [failedOnly, setFailedOnly] = useState(false);
  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticket.id],
    queryFn: () => api.ticketLedger(ticket.id),
    refetchInterval: POLL_MS,
    meta: { errorTitle: "Load this ticket's agent runs" },
  });

  const attempts = useMemo(() => selectMapAttempts(ledger.data, Date.now()), [ledger.data]);

  const logs = useQueries({
    queries: attempts.map((attempt) => ({
      // The same key the Logs tab and the run-log modal use, so one poll feeds all three.
      queryKey: ["run-log", attempt.runId],
      queryFn: () => api.runLog(attempt.runId),
      refetchInterval: ACTIVE_LEDGER_STATUSES.has(attempt.status.toLowerCase()) ? POLL_MS : false,
      meta: { errorTitle: `Load ${attempt.runCode}'s log` },
    })),
    // Structurally shared, so an unchanged poll hands back the same arrays.
    combine: (results) => ({
      lines: results.map((r) => r.data?.lines ?? EMPTY_LINES),
      failed: results.map((r) => r.isError),
      pending: results.map((r) => r.isPending),
      adapter: results.map((r) => r.data?.adapter ?? ""),
      expected: results.map((r) => r.data?.call_lines_expected ?? false),
      startedAt: results.map((r) => r.data?.started_at ?? null),
    }),
  });

  const shownRef = useRef<Record<string, string>>({});
  const views = useMemo(
    () =>
      attempts.map((attempt, i): RunView => {
        const run = {
          ...attempt,
          adapter: logs.adapter[i] ?? "",
          callLinesExpected: logs.expected[i] ?? false,
          startedAt: logs.startedAt[i] ?? null,
          lines: logs.lines[i] ?? EMPTY_LINES,
        };
        return {
          runId: attempt.runId,
          runCode: attempt.runCode,
          name: agentDisplayName(attempt.agentId),
          stageKey: attempt.stageKey,
          status: attempt.status,
          adapter: run.adapter,
          logPending: logs.pending[i] ?? false,
          logFailed: logs.failed[i] ?? false,
          acts: buildToolActs(run, Date.now(), shownRef.current[attempt.runId] ?? null),
        };
      }),
    [attempts, logs.adapter, logs.expected, logs.failed, logs.lines, logs.pending, logs.startedAt],
  );

  // The act each run showed last poll, which is how the model measures a dwell without a clock.
  useEffect(() => {
    for (const view of views) {
      if (view.acts.shown !== null) shownRef.current[view.runId] = view.acts.shown.id;
    }
  }, [views]);

  const model = useMemo(
    () =>
      buildAgentMap(
        // The ledger is the authority on status; a log polled a moment earlier may lag it. A
        // run whose tool calls are not observable contributes no lines: a spoke for it would
        // claim a journey the log cannot show, and its thinking is not a visit to a station.
        attempts.map((attempt, i) => ({
          ...attempt,
          lines: views[i]?.acts.activity === "unknown" ? EMPTY_LINES : (logs.lines[i] ?? EMPTY_LINES),
        })),
      ),
    [attempts, logs.lines, views],
  );

  // Newest first, across every run on the map. Acts are absolutised against each run's own
  // `started_at`, which is the only comparable clock a log line has — and a log line's clock is
  // only accurate to the second, with 40 of this run's calls inside one of them. So the sort is
  // ascending and then reversed, which a stable sort turns into "newest first, and within one
  // second the newest line first"; sorting descending would stand each tied group on its head.
  const acts = useMemo(
    () =>
      views
        .flatMap((view) => view.acts.acts)
        .sort((a, b) => a.lastAtMs - b.lastAtMs)
        .reverse(),
    [views],
  );
  const failedActs = acts.filter((act) => act.state === "failed");
  const pulses = usePulses(views);
  const failedLogs = views.filter((view) => view.logFailed);
  const unknownRuns = views.filter((view) => !view.logPending && view.acts.activity === "unknown");

  if (ledger.isPending) return <PaneSkeleton variant="block" label="Loading agent runs…" />;
  if (ledger.isError && !ledger.data) {
    return (
      <div className="hive-map__notice" role="alert">
        <div className="hive-map__notice-title">Couldn't load this ticket's agent runs</div>
        <div className="hive-map__notice-copy">Retrying every 2 seconds. The Logs tab shows the same runs.</div>
      </div>
    );
  }
  if (attempts.length === 0) {
    return (
      <div className="hive-map__notice">
        <div className="hive-map__notice-title">No agent has run on this ticket yet</div>
        <div className="hive-map__notice-copy">
          Advance the ticket to start its workflow; each run appears here with its tool calls.
        </div>
      </div>
    );
  }

  const showToggle = failedActs.length > 0;
  // At most FEED_ROWS focusable rows in this section, the toggle counted: the feed answers
  // "when, and in what order?" at a fixed keyboard cost, and is never allowed to become a log.
  const rows = (failedOnly ? failedActs : acts).slice(0, FEED_ROWS - (showToggle ? 1 : 0));

  return (
    <div className="hive-map">
      <div className="hive-map__stage">
        <HiveMapPicture model={model} pulses={pulses} unknownRuns={unknownRuns.length} />
      </div>
      <aside className="hive-map__side">
        <section aria-labelledby="hive-map-agents">
          <h3 id="hive-map-agents" className="hive-map__heading">
            {model.anyActive ? `At work · ${model.agents.filter((a) => a.active).length}` : "Last run"}
          </h3>
          <ul className="hive-map__agents">
            {views.map((view) => (
              <li key={view.runId}>
                <AgentRow view={view} onOpen={() => setOpenRunId(view.runId)} />
              </li>
            ))}
          </ul>
          {failedLogs.map((view) => (
            <p className="hive-map__warn" role="status" key={view.runId}>
              {`Couldn't load ${view.runCode}'s log — its activity is unknown, not idle. Retrying every 2 seconds.`}
            </p>
          ))}
        </section>
        <section aria-labelledby="hive-map-feed">
          <h3 id="hive-map-feed" className="hive-map__heading">
            Latest calls
          </h3>
          {showToggle ? (
            <Button
              variant="plain"
              compact
              className="hive-map__filter"
              aria-pressed={failedOnly}
              onClick={() => setFailedOnly((on) => !on)}
            >
              {`Failed only (${failedActs.length})`}
            </Button>
          ) : null}
          {failedOnly ? (
            <p className="hive-map__filter-status" role="status">
              {`Showing ${failedActs.length} failed acts of ${acts.length}`}
            </p>
          ) : null}
          {rows.length === 0 ? (
            <FeedEmpty unknownAdapters={unknownRuns.map((view) => view.adapter)} allUnknown={unknownRuns.length === views.length} />
          ) : (
            <ol className="hive-map__feed" aria-live="polite">
              {rows.map((act) => (
                <li key={act.id}>
                  <FeedRow act={act} onOpen={() => setOpenRunId(act.runId)} />
                </li>
              ))}
            </ol>
          )}
        </section>
      </aside>
      <RunLogModal runId={openRunId} onClose={() => setOpenRunId(null)} />
    </div>
  );
}

/** One run as the side lists read it: the ledger's facts, plus what its own log says. */
interface RunView {
  runId: string;
  runCode: string;
  name: string;
  stageKey: string;
  status: string;
  adapter: string;
  logPending: boolean;
  logFailed: boolean;
  acts: ToolActsModel;
}

/** Acts that appeared since the last poll, each kept for one flight across its spoke. */
function usePulses(views: readonly RunView[]): ToolAct[] {
  const seenRef = useRef<Set<string> | null>(null);
  const timersRef = useRef<number[]>([]);
  const [pulses, setPulses] = useState<ToolAct[]>([]);
  // One pulse per act, never one per call: a 40-call burst at one place is one journey, and
  // forty would be the picture claiming journeys the agent never made. A desk act flies
  // nothing — the desk is where the agent already sits.
  const flying = views.flatMap((view) => view.acts.acts.filter((act) => isRingPlace(act.place)));

  useEffect(() => {
    const seen = seenRef.current;
    if (seen === null) {
      // The history already on screen is not news; only what arrives from now on flies.
      seenRef.current = new Set(flying.map((act) => act.id));
      return;
    }
    const fresh = flying.filter((act) => !seen.has(act.id));
    if (fresh.length === 0) return;
    for (const act of fresh) seen.add(act.id);
    setPulses((current) => [...current, ...fresh]);
    const freshIds = new Set(fresh.map((act) => act.id));
    // Not this effect's cleanup: the next poll re-runs the effect long before a
    // flight lands, and clearing the timer then would strand the pulse on screen.
    timersRef.current.push(
      window.setTimeout(() => {
        setPulses((current) => current.filter((act) => !freshIds.has(act.id)));
      }, PULSE_MS + 100),
    );
  }, [flying]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, []);

  return pulses;
}

/**
 * What this run is doing, in one line.
 *
 * Observability comes first: a run whose calls cannot be seen is never described as idle,
 * whatever its status. Then the run's own status, which wins over any act with no dwell. Only
 * a run still at work shows what it is doing.
 */
function nowLine(view: RunView): string {
  if (view.logPending) return "loading activity…";
  if (view.logFailed) return "activity unknown — its log could not be loaded";
  if (view.acts.activity === "unknown") return unknownActivity(view.adapter);
  if (view.acts.activity === "no-calls-yet") return "no calls yet";
  const status = STATUS_NOW_LINE[view.status.toLowerCase()];
  if (view.acts.phase !== "active") return status ?? `✕ ${view.status}`;
  const shown = view.acts.shown;
  return shown === null ? "no calls yet" : `${STATE_MARK[shown.state]} ${placeLabel(shown.place)} — ${shown.label}`;
}

function AgentRow({ view, onOpen }: { view: RunView; onOpen: () => void }) {
  const now = nowLine(view);
  const calls = view.acts.acts.reduce((total, act) => total + act.callsInLog, 0);
  const failures = view.acts.acts.filter((act) => act.state === "failed").length;
  // One source for "still at work": the act model, which read the same status.
  const active = view.acts.phase !== "ended";
  const busy = active && view.acts.shown?.state === "running";
  // An unknown run's meta ends on its separator and the now-line below finishes the sentence:
  // the row reads "review · succeeded · activity unknown — the cursor adapter does not log tool
  // calls" as one statement, in place of the "· N calls" a run with observable calls gets. Two
  // copies of "activity unknown" in one row would be the same nothing said twice.
  const meta = view.logPending
    ? `${view.stageKey} · ${view.status}`
    : view.acts.activity === "unknown"
      ? `${view.stageKey} · ${view.status} · `
      : `${view.stageKey} · ${view.status} · ${calls} calls${failures > 0 ? ` · ${failures} failed` : ""}`;

  return (
    <Button
      variant="plain"
      className={`hive-map__agent-row${active ? " is-active" : ""}`}
      onClick={onOpen}
      aria-busy={busy ? true : undefined}
      aria-label={`Open ${view.name}'s log (${view.runCode}). ${view.stageKey}, ${inWords(now)}`}
    >
      <span className="hive-map__agent-name">
        <span className="hive-map__agent-initials" aria-hidden>
          {initials(view.name)}
        </span>
        {view.name}
      </span>
      <span className="hive-map__agent-meta">{meta}</span>
      <span className="hive-map__agent-now">{now}</span>
    </Button>
  );
}

function FeedRow({ act, onOpen }: { act: ToolAct; onOpen: () => void }) {
  const where = placeLabel(act.place);
  const more = act.callsInLog - 1;
  return (
    <Button
      variant="plain"
      className={`hive-map__feed-row hive-map__feed-row--${act.state}`}
      onClick={onOpen}
      aria-busy={act.state === "running" ? true : undefined}
      aria-label={`${agentDisplayName(act.agentId)}: ${where}, ${act.label}, ${act.state}. Open its log`}
    >
      <span className="hive-map__feed-mark" aria-hidden>
        {STATE_MARK[act.state]}
      </span>
      <span className="hive-map__feed-time">{act.time}</span>
      <span className={`hive-map__feed-station hive-map__feed-station--${act.place}`}>{where}</span>
      <span className="hive-map__feed-label">{act.label}</span>
      {more > 0 ? (
        <span
          className="hive-map__feed-fold"
          aria-hidden
          title={`+${more} more calls at ${where} in this act, of the log's ${LOG_TAIL_LINES} most recent lines`}
        >
          +{more}
        </span>
      ) : null}
      <span className="hive-map__feed-agent">{agentDisplayName(act.agentId)}</span>
    </Button>
  );
}

/**
 * Nothing in the feed, which is three different situations. When every run's calls are
 * unobservable the copy says so and points at the row button, because that reading is neither
 * failure nor idleness and the operator still has somewhere to go.
 */
function FeedEmpty({ unknownAdapters, allUnknown }: { unknownAdapters: readonly string[]; allUnknown: boolean }) {
  if (!allUnknown) {
    return (
      <p className="hive-map__empty">
        No tool calls logged yet. Runs from before tool calls were logged show results only.
      </p>
    );
  }
  const named = [...new Set(unknownAdapters.filter(Boolean))];
  const which =
    named.length === 0
      ? "no adapter is recorded for these runs, so nothing here can say whether they log tool calls"
      : `the ${named.join(" and ")} adapter${named.length > 1 ? "s do" : " does"} not log tool calls`;
  return (
    <p className="hive-map__empty" role="status">
      {`Nothing to show: ${which} — open a run above to read its log and see what it did.`}
    </p>
  );
}

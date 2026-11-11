import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LogLine } from "../../../api/runLogTypes";
import {
  MAP_STATIONS,
  MAP_VIEW,
  agentPoint,
  buildAgentMap,
  selectMapAttempts,
  spokeWidth,
  stationPoint,
  type AgentMapModel,
  type MapAgent,
  type MapCall,
} from "../../../lib/hive/agentMap";
import { ACTIVE_LEDGER_STATUSES } from "../../../lib/ledgerStatus";
import { RunLogModal } from "../../RunLogModal";
import { Button } from "../../ui/Button";
import { PaneSkeleton } from "../../ui/PaneSkeleton";
import "./HiveAgentMap.css";

const POLL_MS = 2000;
const EMPTY_LINES: LogLine[] = [];
/** How long one pulse takes to cross a spoke; matches `hive-map-pulse` in the CSS. */
const PULSE_MS = 900;

const STATION_INDEX = new Map(MAP_STATIONS.map((s, i) => [s.id, i]));

/**
 * Hive's map view: every agent run on this ticket, and each tool call it makes
 * flying to the station it belongs to (files, shell, network, memory, …).
 *
 * The SVG is a picture; everything it says is also in the DOM lists beside it,
 * which are what a keyboard, a screen reader or an agent operates.
 */
export function HiveAgentMap({ ticket }: { ticket: TicketDetail }) {
  const [openRunId, setOpenRunId] = useState<string | null>(null);
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
    }),
  });

  const model = useMemo(
    () =>
      buildAgentMap(
        // The ledger is the authority on status; a log polled a moment earlier may lag it.
        attempts.map((attempt, i) => ({ ...attempt, lines: logs.lines[i] ?? EMPTY_LINES })),
      ),
    [attempts, logs.lines],
  );
  const pulses = usePulses(model);
  const failedLogs = attempts.filter((_, i) => logs.failed[i]).map((a) => a.runCode);

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

  return (
    <div className="hive-map">
      <div className="hive-map__stage">
        <MapPicture model={model} pulses={pulses} />
      </div>
      <aside className="hive-map__side">
        <section aria-labelledby="hive-map-agents">
          <h3 id="hive-map-agents" className="hive-map__heading">
            {model.anyActive ? `At work · ${model.agents.filter((a) => a.active).length}` : "Last run"}
          </h3>
          <ul className="hive-map__agents">
            {model.agents.map((agent) => (
              <li key={agent.runId}>
                <AgentRow agent={agent} onOpen={() => setOpenRunId(agent.runId)} />
              </li>
            ))}
          </ul>
          {failedLogs.length > 0 ? (
            <p className="hive-map__warn" role="status">
              No log yet for {failedLogs.join(", ")}; its calls will appear once the log loads.
            </p>
          ) : null}
        </section>
        <section aria-labelledby="hive-map-feed">
          <h3 id="hive-map-feed" className="hive-map__heading">
            Latest calls
          </h3>
          {model.feed.length === 0 ? (
            <p className="hive-map__empty">
              No tool calls logged yet. Runs from before tool calls were logged show results only.
            </p>
          ) : (
            <ol className="hive-map__feed" aria-live="polite">
              {model.feed.map((call) => (
                <li key={call.id}>
                  <FeedRow call={call} onOpen={() => setOpenRunId(call.runId)} />
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

/** Calls that appeared since the last poll, each kept for one flight across its spoke. */
function usePulses(model: AgentMapModel): MapCall[] {
  const seenRef = useRef<Set<string> | null>(null);
  const timersRef = useRef<number[]>([]);
  const [pulses, setPulses] = useState<MapCall[]>([]);

  useEffect(() => {
    const seen = seenRef.current;
    if (seen === null) {
      // The history already on screen is not news; only what arrives from now on flies.
      seenRef.current = new Set(model.calls.map((c) => c.id));
      return;
    }
    const fresh = model.calls.filter((c) => !seen.has(c.id));
    if (fresh.length === 0) return;
    for (const c of fresh) seen.add(c.id);
    setPulses((current) => [...current, ...fresh]);
    const freshIds = new Set(fresh.map((c) => c.id));
    // Not this effect's cleanup: the next poll re-runs the effect long before a
    // flight lands, and clearing the timer then would strand the pulse on screen.
    timersRef.current.push(
      window.setTimeout(() => {
        setPulses((current) => current.filter((c) => !freshIds.has(c.id)));
      }, PULSE_MS + 100),
    );
  }, [model.calls]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, []);

  return pulses;
}

function MapPicture({ model, pulses }: { model: AgentMapModel; pulses: MapCall[] }) {
  const agentIndex = new Map(model.agents.map((a, i) => [a.runId, i]));
  const count = model.agents.length;
  const busiest = [...model.stations].sort((a, b) => b.uses - a.uses).filter((s) => s.uses > 0).slice(0, 3);
  const summary =
    busiest.length === 0
      ? "Station map with no tool calls yet."
      : `Station map. Most used: ${busiest.map((s) => `${s.station.label} ${s.uses}`).join(", ")}.`;

  return (
    <svg className="hive-map__svg" viewBox={`0 0 ${MAP_VIEW.size} ${MAP_VIEW.size}`} role="img" aria-label={summary}>
      <circle className="hive-map__orbit" cx={MAP_VIEW.centre} cy={MAP_VIEW.centre} r={MAP_VIEW.stationRadius} />

      {model.stations.flatMap((state, si) => {
        const to = stationPoint(si);
        return model.agents
          .filter((agent) => (state.usesByRun[agent.runId] ?? 0) > 0)
          .map((agent) => {
            const from = agentPoint(agentIndex.get(agent.runId) ?? 0, count);
            return (
              <line
                key={`${state.station.id}-${agent.runId}`}
                className={`hive-map__spoke hive-map__spoke--${state.station.id}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                strokeWidth={spokeWidth(state.usesByRun[agent.runId] ?? 0)}
              />
            );
          });
      })}

      {pulses.map((call) => {
        const from = agentPoint(agentIndex.get(call.runId) ?? 0, count);
        const to = stationPoint(STATION_INDEX.get(call.station) ?? 0);
        return (
          <line
            key={call.id}
            className={`hive-map__pulse hive-map__pulse--${call.station}${call.state === "failed" ? " hive-map__pulse--failed" : ""}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            pathLength={100}
          />
        );
      })}

      {model.stations.map((state, si) => {
        const p = stationPoint(si);
        const lit = state.uses > 0;
        return (
          <g
            key={state.station.id}
            className={`hive-map__station hive-map__station--${state.station.id}${lit ? " is-lit" : ""}`}
            transform={`translate(${p.x} ${p.y})`}
          >
            <title>{`${state.station.label}: ${state.station.hint}. ${state.uses} calls${state.running ? `, ${state.running} running` : ""}${state.failures ? `, ${state.failures} failed` : ""}.`}</title>
            <circle className="hive-map__station-disc" r={19} />
            {state.running > 0 ? <circle className="hive-map__station-busy" r={23} /> : null}
            <path className="hive-map__station-icon" d={state.station.icon} />
            <text className="hive-map__station-label" y={34} textAnchor="middle">
              {state.station.label}
            </text>
            {state.uses > 0 ? (
              <text className="hive-map__station-count" y={46} textAnchor="middle">
                {state.uses}
              </text>
            ) : null}
            {state.running > 1 ? (
              <text className="hive-map__station-multi" x={16} y={-14}>
                ×{state.running}
              </text>
            ) : null}
            {state.failures > 0 ? (
              <g transform="translate(-15 -15)">
                <circle className="hive-map__station-fail" r={7} />
                <text className="hive-map__station-fail-count" y={3.5} textAnchor="middle">
                  {state.failures}
                </text>
              </g>
            ) : null}
          </g>
        );
      })}

      {model.agents.map((agent, i) => {
        const p = agentPoint(i, count);
        const solo = count === 1;
        return (
          <g
            key={agent.runId}
            className={`hive-map__agent${agent.active ? " is-active" : ""}`}
            transform={`translate(${p.x} ${p.y})`}
          >
            <title>{`${agent.name} (${agent.stageKey}): ${agent.status}`}</title>
            <circle className="hive-map__agent-disc" r={solo ? 30 : 15} />
            <text className="hive-map__agent-label" y={4} textAnchor="middle">
              {solo ? agent.runCode.replace(/^run_/, "") : initials(agent.name)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function initials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

const STATE_MARK: Record<MapCall["state"], string> = { running: "●", done: "·", failed: "✕" };

function AgentRow({ agent, onOpen }: { agent: MapAgent; onOpen: () => void }) {
  const current = agent.current;
  return (
    <Button
      variant="plain"
      className={`hive-map__agent-row${agent.active ? " is-active" : ""}`}
      onClick={onOpen}
      aria-label={`Open ${agent.name}'s log (${agent.runCode})`}
    >
      <span className="hive-map__agent-name">
        <span className="hive-map__agent-initials" aria-hidden>
          {initials(agent.name)}
        </span>
        {agent.name}
      </span>
      <span className="hive-map__agent-meta">
        {agent.stageKey} · {agent.status} · {agent.calls} calls
        {agent.failures > 0 ? ` · ${agent.failures} failed` : ""}
      </span>
      <span className="hive-map__agent-now">
        {current ? `${STATE_MARK[current.state]} ${stationLabel(current.station)} — ${current.label}` : "no calls yet"}
      </span>
    </Button>
  );
}

function stationLabel(id: MapCall["station"]): string {
  return MAP_STATIONS[STATION_INDEX.get(id) ?? 0]?.label ?? id;
}

function FeedRow({ call, onOpen }: { call: MapCall; onOpen: () => void }) {
  return (
    <Button
      variant="plain"
      className={`hive-map__feed-row hive-map__feed-row--${call.state}`}
      onClick={onOpen}
      aria-label={`${call.agentName}: ${stationLabel(call.station)}, ${call.label}, ${call.state}. Open its log`}
    >
      <span className="hive-map__feed-mark" aria-hidden>
        {STATE_MARK[call.state]}
      </span>
      <span className="hive-map__feed-time">{call.time}</span>
      <span className={`hive-map__feed-station hive-map__feed-station--${call.station}`}>{stationLabel(call.station)}</span>
      <span className="hive-map__feed-label">{call.label}</span>
      {call.folded > 0 ? <span className="hive-map__feed-fold">+{call.folded}</span> : null}
      <span className="hive-map__feed-agent">{call.agentName}</span>
    </Button>
  );
}

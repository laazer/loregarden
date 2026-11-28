/**
 * The map's picture: the ring of stations, a worn spoke per run that went there, and one
 * pulse per act in flight.
 *
 * It is a picture, and it says nothing the DOM lists beside it do not — those are what a
 * keyboard, a screen reader or an agent operates. Two things it deliberately never draws: the
 * desk, which is the agent's own position rather than a ring station, and a run whose activity
 * is unknown, which gets no spoke, no pulse and no busy ring because nothing is known to draw.
 */

import {
  MAP_STATIONS,
  MAP_VIEW,
  agentPoint,
  spokeWidth,
  stationPoint,
  type AgentMapModel,
} from "../../../lib/hive/agentMap";
import type { ToolAct } from "../../../lib/hive/toolActs";
import { initials } from "./hiveMapText";

const STATION_INDEX = new Map(MAP_STATIONS.map((station, index) => [station.id, index]));

function summarise(model: AgentMapModel, unknownRuns: number): string {
  const busiest = model.places
    .filter((state) => state.place.ring && state.uses > 0)
    .sort((a, b) => b.uses - a.uses)
    .slice(0, 3);
  const head =
    busiest.length === 0
      ? "Station map with no tool calls yet."
      : `Station map. Most used: ${busiest.map((s) => `${s.place.label} ${s.uses}`).join(", ")}.`;
  return unknownRuns === 0 ? head : `${head} ${unknownRuns} runs with unknown activity.`;
}

export function HiveMapPicture({
  model,
  pulses,
  unknownRuns,
}: {
  model: AgentMapModel;
  pulses: ToolAct[];
  /** Runs whose tool calls are not observable, named in the label because nothing draws them. */
  unknownRuns: number;
}) {
  const agentIndex = new Map(model.agents.map((agent, index) => [agent.runId, index]));
  const count = model.agents.length;
  const stations = model.places.filter((state) => state.place.ring);

  return (
    <svg
      className="hive-map__svg"
      viewBox={`0 0 ${MAP_VIEW.size} ${MAP_VIEW.size}`}
      role="img"
      aria-label={summarise(model, unknownRuns)}
    >
      <circle className="hive-map__orbit" cx={MAP_VIEW.centre} cy={MAP_VIEW.centre} r={MAP_VIEW.stationRadius} />

      {stations.flatMap((state, si) => {
        const to = stationPoint(si);
        return model.agents
          .filter((agent) => (state.usesByRun[agent.runId] ?? 0) > 0)
          .map((agent) => {
            const from = agentPoint(agentIndex.get(agent.runId) ?? 0, count);
            return (
              <line
                key={`${state.place.id}-${agent.runId}`}
                className={`hive-map__spoke hive-map__spoke--${state.place.id}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                strokeWidth={spokeWidth(state.usesByRun[agent.runId] ?? 0)}
              />
            );
          });
      })}

      {pulses.map((act) => {
        const from = agentPoint(agentIndex.get(act.runId) ?? 0, count);
        const to = stationPoint(STATION_INDEX.get(act.place) ?? 0);
        return (
          <line
            key={act.id}
            className={`hive-map__pulse hive-map__pulse--${act.place}${act.state === "failed" ? " hive-map__pulse--failed" : ""}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            pathLength={100}
          />
        );
      })}

      {stations.map((state, si) => {
        const point = stationPoint(si);
        const lit = state.uses > 0;
        return (
          <g
            key={state.place.id}
            className={`hive-map__station hive-map__station--${state.place.id}${lit ? " is-lit" : ""}`}
            transform={`translate(${point.x} ${point.y})`}
          >
            <title>{`${state.place.label}: ${state.place.hint}. ${state.uses} calls${state.running ? `, ${state.running} running` : ""}${state.failures ? `, ${state.failures} failed` : ""}.`}</title>
            <circle className="hive-map__station-disc" r={19} />
            {state.running > 0 ? <circle className="hive-map__station-busy" r={23} /> : null}
            <path className="hive-map__station-icon" d={state.place.icon} />
            <text className="hive-map__station-label" y={34} textAnchor="middle">
              {state.place.label}
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

      {model.agents.map((agent, index) => {
        const point = agentPoint(index, count);
        const solo = count === 1;
        return (
          <g
            key={agent.runId}
            className={`hive-map__agent${agent.active ? " is-active" : ""}`}
            transform={`translate(${point.x} ${point.y})`}
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

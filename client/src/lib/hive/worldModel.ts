import type { StageStatus, WorkflowStageView } from "../../api/client";
import { stageStatusMeta } from "../stageDisplay";
import { mapAgentToRole, type HiveCastVariant } from "./roleMap";
import {
  HIVE_SKINS,
  HIVE_STATION_IDS,
  type HiveArtifactKind,
  type HiveEventKind,
  type HiveSkinId,
  type HiveStationId,
  skinLabel,
} from "./skins";

const STATUS_RANK: Record<StageStatus, number> = {
  running: 5,
  blocked: 4,
  awaiting: 3,
  done: 2,
  pending: 1,
  wont_do: 0,
};

/** Fixed world positions in tile units (16px tiles). Map is 40×28 tiles. */
export const HIVE_MAP = {
  tileSize: 16,
  width: 40,
  height: 28,
} as const;

export const STATION_POSITIONS: Record<HiveStationId, { x: number; y: number }> = {
  planner_hq: { x: 20, y: 3 },
  research: { x: 6, y: 10 },
  coding: { x: 20, y: 12 },
  testing: { x: 34, y: 10 },
  deploy: { x: 20, y: 22 },
};

export const WAITING_POSITION = { x: 8, y: 20 };
export const DESK_ROW = [
  { x: 10, y: 16 },
  { x: 14, y: 16 },
  { x: 18, y: 16 },
  { x: 22, y: 16 },
  { x: 26, y: 16 },
  { x: 30, y: 16 },
] as const;

export type HiveAgentMotion =
  | "idle"
  | "walking"
  | "working"
  | "waiting"
  | "success"
  | "ghost";

export interface HiveAgentState {
  id: string;
  name: string;
  status: StageStatus;
  statusLabel: string;
  stage: string;
  skill: string;
  station: HiveStationId;
  cast: HiveCastVariant;
  motion: HiveAgentMotion;
  target: { x: number; y: number };
  desk: { x: number; y: number };
  color: string;
  showTool: boolean;
  active: boolean;
  pulsing: boolean;
}

export interface HiveStationState {
  id: HiveStationId;
  label: string;
  x: number;
  y: number;
  active: boolean;
  occupiedBy: string[];
}

export interface HiveArtifactFlight {
  id: string;
  kind: HiveArtifactKind;
  label: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
  /** Stable key for transition identity; scene uses this to avoid replaying forever. */
  triggerKey: string;
}

export interface HiveWorldEvent {
  kind: HiveEventKind;
  label: string;
  /** Station the overlay attaches to (error) or waiting prop position. */
  at: { x: number; y: number };
  stationId?: HiveStationId;
}

export interface HiveWorldModel {
  skin: HiveSkinId;
  floorTitle: string;
  agents: HiveAgentState[];
  stations: HiveStationState[];
  flights: HiveArtifactFlight[];
  events: HiveWorldEvent[];
  orchestratorActive: boolean;
  orchestratorLabel: string;
  waitingProp: { x: number; y: number; label: string; visible: boolean };
  idle: boolean;
}

export interface BuildHiveWorldOptions {
  skin: HiveSkinId;
  hasErrorArtifact?: boolean;
  /** Previous agent status snapshot for flight detection: agentId → status */
  previousStatuses?: Record<string, StageStatus>;
}

function isHumanAgent(agentId: string): boolean {
  const normalized = agentId.toLowerCase();
  return !normalized || normalized === "—" || normalized.includes("human");
}

function formatAgentName(agentId: string): string {
  return agentId
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function motionForStatus(status: StageStatus): HiveAgentMotion {
  switch (status) {
    case "running":
      return "working";
    case "awaiting":
    case "blocked":
      return "waiting";
    case "done":
      return "success";
    case "wont_do":
      return "ghost";
    default:
      return "idle";
  }
}

function targetForAgent(
  status: StageStatus,
  station: HiveStationId,
  desk: { x: number; y: number },
): { x: number; y: number } {
  if (status === "running") {
    return { ...STATION_POSITIONS[station] };
  }
  if (status === "awaiting" || status === "blocked") {
    return { ...WAITING_POSITION };
  }
  if (status === "done") {
    return { ...STATION_POSITIONS.planner_hq };
  }
  return { ...desk };
}

export function buildHiveWorld(
  stages: WorkflowStageView[],
  options: BuildHiveWorldOptions,
): HiveWorldModel {
  const { skin, hasErrorArtifact = false, previousStatuses = {} } = options;
  const agentMap = new Map<
    string,
    { agentId: string; status: StageStatus; stage: string; skill: string }
  >();

  for (const stage of stages) {
    const agentId = stage.agent_id?.trim();
    if (!agentId || isHumanAgent(agentId) || stage.stage_type === "gate") continue;

    const existing = agentMap.get(agentId);
    if (!existing || STATUS_RANK[stage.status] > STATUS_RANK[existing.status]) {
      agentMap.set(agentId, {
        agentId,
        status: stage.status,
        stage: stage.name,
        skill: stage.skill_name,
      });
    }
  }

  const entries = [...agentMap.values()].slice(0, DESK_ROW.length);
  const occupation = new Map<HiveStationId, string[]>();

  const agents: HiveAgentState[] = entries.map((entry, index) => {
    const role = mapAgentToRole(entry.agentId);
    const desk = DESK_ROW[index] ?? DESK_ROW[0];
    const meta = stageStatusMeta(entry.status);
    const active =
      entry.status === "running" ||
      entry.status === "blocked" ||
      entry.status === "awaiting";
    const motion = motionForStatus(entry.status);
    const target = targetForAgent(entry.status, role.station, desk);

    if (entry.status === "running") {
      const list = occupation.get(role.station) ?? [];
      list.push(entry.agentId);
      occupation.set(role.station, list);
    }

    return {
      id: entry.agentId,
      name: formatAgentName(entry.agentId),
      status: entry.status,
      statusLabel: meta.label,
      stage: entry.stage,
      skill: entry.skill,
      station: role.station,
      cast: role.cast,
      motion,
      target,
      desk: { ...desk },
      color: meta.dot,
      showTool: active && Boolean(entry.skill),
      active,
      pulsing: entry.status === "running",
    };
  });

  const stations: HiveStationState[] = HIVE_STATION_IDS.map((id) => {
    const pos = STATION_POSITIONS[id];
    const occupiedBy = occupation.get(id) ?? [];
    return {
      id,
      label: skinLabel(skin, id),
      x: pos.x,
      y: pos.y,
      active: occupiedBy.length > 0,
      occupiedBy,
    };
  });

  const flights: HiveArtifactFlight[] = [];
  for (const agent of agents) {
    const prev = previousStatuses[agent.id];
    if (!prev || prev === agent.status) continue;

    if (prev === "pending" && agent.status === "running") {
      flights.push({
        id: `${agent.id}-ctx-${agent.status}`,
        kind: "context",
        label: skinLabel(skin, "context"),
        from: { ...STATION_POSITIONS.planner_hq },
        to: { ...STATION_POSITIONS[agent.station] },
        triggerKey: `${agent.id}:pending->running`,
      });
    }

    if (prev === "running" && (agent.status === "done" || agent.status === "blocked")) {
      flights.push({
        id: `${agent.id}-diff-${agent.status}`,
        kind: "diff",
        label: skinLabel(skin, "diff"),
        from: { ...STATION_POSITIONS[agent.station] },
        to: { ...STATION_POSITIONS.planner_hq },
        triggerKey: `${agent.id}:running->${agent.status}`,
      });
    }
  }

  const hasWaiting = agents.some(
    (a) => a.status === "awaiting" || a.status === "blocked",
  );
  const hasBlocked = agents.some((a) => a.status === "blocked");
  const events: HiveWorldEvent[] = [];

  if (hasWaiting) {
    events.push({
      kind: "waiting",
      label: skinLabel(skin, "waiting"),
      at: { ...WAITING_POSITION },
    });
  }

  if (hasBlocked || hasErrorArtifact) {
    const errorStation: HiveStationId =
      agents.find((a) => a.status === "blocked")?.station ?? "coding";
    events.push({
      kind: "error",
      label: skinLabel(skin, "error"),
      at: { ...STATION_POSITIONS[errorStation] },
      stationId: errorStation,
    });
  }

  const orchestratorActive = agents.some((a) => a.active);

  return {
    skin,
    floorTitle: HIVE_SKINS[skin].floorTitle,
    agents,
    stations,
    flights,
    events,
    orchestratorActive,
    orchestratorLabel: skinLabel(skin, "planner_hq"),
    waitingProp: {
      x: WAITING_POSITION.x,
      y: WAITING_POSITION.y,
      label: skinLabel(skin, "waiting"),
      visible: hasWaiting,
    },
    idle: agents.length === 0,
  };
}

/** Snapshot helper for transition detection across polls. */
export function agentStatusSnapshot(agents: HiveAgentState[]): Record<string, StageStatus> {
  const out: Record<string, StageStatus> = {};
  for (const agent of agents) {
    out[agent.id] = agent.status;
  }
  return out;
}

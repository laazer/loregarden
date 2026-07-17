import type { StageStatus, WorkflowStageView } from "../../api/client";
import { stageStatusMeta } from "../stageDisplay";
import { getHiveLayout, type HiveLayout } from "./layouts";
import { mapAgentToRole, type HiveCastVariant } from "./roleMap";
import {
  HIVE_SKINS,
  HIVE_STATION_IDS,
  type HiveArtifactKind,
  type HiveEventKind,
  type HiveSkinId,
  type HiveStationId,
  resolveHiveSkinId,
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

/** Default world positions in tile units (16px tiles). Map is 40×28 tiles. */
export const HIVE_MAP = {
  tileSize: 16,
  width: 40,
  height: 28,
} as const;

export const STATION_POSITIONS = getHiveLayout("officeplace").stationPositions;

export const WAITING_POSITION = getHiveLayout("officeplace").waitingPosition;

export const DESK_ROW = getHiveLayout("officeplace").deskRow;

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
  layout: HiveLayout;
  agents: HiveAgentState[];
  stations: HiveStationState[];
  flights: HiveArtifactFlight[];
  events: HiveWorldEvent[];
  orchestratorActive: boolean;
  orchestratorLabel: string;
  waitingProp: { x: number; y: number; label: string; visible: boolean };
  receptionist: { id: string; x: number; y: number; label: string } | null;
  idle: boolean;
}

export interface BuildHiveWorldOptions {
  skin: HiveSkinId | string;
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
  layout: HiveLayout,
): { x: number; y: number } {
  if (status === "running") {
    return { ...layout.stationPositions[station] };
  }
  if (status === "awaiting" || status === "blocked") {
    return { ...layout.waitingPosition };
  }
  if (status === "done") {
    return { ...layout.stationPositions.planner_hq };
  }
  return { ...desk };
}

export function buildHiveWorld(
  stages: WorkflowStageView[],
  options: BuildHiveWorldOptions,
): HiveWorldModel {
  const skin = resolveHiveSkinId(options.skin);
  const { hasErrorArtifact = false, previousStatuses = {} } = options;
  const layout = getHiveLayout(skin);
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

  const entries = [...agentMap.values()].slice(0, layout.deskRow.length);
  const occupation = new Map<HiveStationId, string[]>();

  const agents: HiveAgentState[] = entries.map((entry, index) => {
    const role = mapAgentToRole(entry.agentId);
    const desk = layout.deskRow[index] ?? layout.deskRow[0];
    const meta = stageStatusMeta(entry.status);
    const active =
      entry.status === "running" ||
      entry.status === "blocked" ||
      entry.status === "awaiting";
    const motion = motionForStatus(entry.status);
    const target = targetForAgent(entry.status, role.station, desk, layout);

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
    const pos = layout.stationPositions[id];
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
        from: { ...layout.stationPositions.planner_hq },
        to: { ...layout.stationPositions[agent.station] },
        triggerKey: `${agent.id}:pending->running`,
      });
    }

    if (prev === "running" && (agent.status === "done" || agent.status === "blocked")) {
      flights.push({
        id: `${agent.id}-diff-${agent.status}`,
        kind: "diff",
        label: skinLabel(skin, "diff"),
        from: { ...layout.stationPositions[agent.station] },
        to: { ...layout.stationPositions.planner_hq },
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
      at: { ...layout.waitingPosition },
    });
  }

  if (hasBlocked || hasErrorArtifact) {
    const errorStation: HiveStationId =
      agents.find((a) => a.status === "blocked")?.station ?? "coding";
    events.push({
      kind: "error",
      label: skinLabel(skin, "error"),
      at: { ...layout.stationPositions[errorStation] },
      stationId: errorStation,
    });
  }

  const orchestratorActive = agents.some((a) => a.active);

  return {
    skin,
    floorTitle: HIVE_SKINS[skin].floorTitle,
    layout,
    agents,
    stations,
    flights,
    events,
    orchestratorActive,
    orchestratorLabel: skinLabel(skin, "planner_hq"),
    waitingProp: {
      x: layout.waitingPosition.x,
      y: layout.waitingPosition.y,
      label: skinLabel(skin, "waiting"),
      visible: hasWaiting,
    },
    receptionist: layout.receptionist ? { ...layout.receptionist } : null,
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

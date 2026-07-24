import type { StageStatus, WorkflowStageView } from "../../api/client";
import { stageStatusMeta } from "../stageDisplay";
import { getHiveLayout, type HiveLayout } from "./layouts";
import { crewForStation, type HiveCharacterId } from "./cast";
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
  /** Unique per body. A crewed agent contributes several bodies. */
  id: string;
  /** The orchestrator agent this body belongs to; shared across its crew. */
  agentId: string;
  /**
   * Lead body of the crew. Only the lead shows a status card and drives
   * artifact flights, so a crewed agent still reads as one agent.
   */
  lead: boolean;
  /** Named sprite, when the skin staffs stations with a cast. */
  character: HiveCharacterId | null;
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
  receptionist: {
    id: string;
    x: number;
    y: number;
    label: string;
    character: HiveCharacterId;
  } | null;
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

  const entries = [...agentMap.values()];
  const occupation = new Map<HiveStationId, string[]>();

  // Desks are a finite row shared by every body on the floor. A crewed agent
  // claims one per member, so walk a single cursor and stop when the row runs
  // out rather than letting later bodies collapse onto desk zero.
  const agents: HiveAgentState[] = [];
  let deskCursor = 0;

  for (const entry of entries) {
    const role = mapAgentToRole(entry.agentId);
    const meta = stageStatusMeta(entry.status);
    const active =
      entry.status === "running" ||
      entry.status === "blocked" ||
      entry.status === "awaiting";
    const motion = motionForStatus(entry.status);

    if (entry.status === "running") {
      const list = occupation.get(role.station) ?? [];
      list.push(entry.agentId);
      occupation.set(role.station, list);
    }

    const crew = crewForStation(skin, role.station);
    const members: (HiveCharacterId | null)[] = crew ?? [null];

    for (const [memberIndex, character] of members.entries()) {
      const desk = layout.deskRow[deskCursor];
      if (!desk) break;
      deskCursor += 1;

      agents.push({
        id: character ? `${entry.agentId}#${character}` : entry.agentId,
        agentId: entry.agentId,
        lead: memberIndex === 0,
        character,
        name: formatAgentName(entry.agentId),
        status: entry.status,
        statusLabel: meta.label,
        stage: entry.stage,
        skill: entry.skill,
        station: role.station,
        cast: role.cast,
        motion,
        target: targetForAgent(entry.status, role.station, desk, layout),
        desk: { ...desk },
        color: meta.dot,
        showTool: active && Boolean(entry.skill),
        active,
        pulsing: entry.status === "running",
      });
    }

    if (deskCursor >= layout.deskRow.length) break;
  }

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
  // Keyed on agentId, and only for the lead: previousStatuses is a per-agent
  // snapshot, so iterating every body would fire one duplicate flight per
  // crew member on the same status change.
  for (const agent of agents) {
    if (!agent.lead) continue;
    const prev = previousStatuses[agent.agentId];
    if (!prev || prev === agent.status) continue;

    if (prev === "pending" && agent.status === "running") {
      flights.push({
        id: `${agent.agentId}-ctx-${agent.status}`,
        kind: "context",
        label: skinLabel(skin, "context"),
        from: { ...layout.stationPositions.planner_hq },
        to: { ...layout.stationPositions[agent.station] },
        triggerKey: `${agent.agentId}:pending->running`,
      });
    }

    if (prev === "running" && (agent.status === "done" || agent.status === "blocked")) {
      flights.push({
        id: `${agent.agentId}-diff-${agent.status}`,
        kind: "diff",
        label: skinLabel(skin, "diff"),
        from: { ...layout.stationPositions[agent.station] },
        to: { ...layout.stationPositions.planner_hq },
        triggerKey: `${agent.agentId}:running->${agent.status}`,
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
/**
 * Snapshot for the next frame's flight detection. Keyed by agentId, not by the
 * per-body id: buildHiveWorld looks transitions up per agent, so keying by body
 * would make every lookup miss and silently stop all flights on crewed skins.
 */
export function agentStatusSnapshot(agents: HiveAgentState[]): Record<string, StageStatus> {
  const out: Record<string, StageStatus> = {};
  for (const agent of agents) {
    out[agent.agentId] = agent.status;
  }
  return out;
}

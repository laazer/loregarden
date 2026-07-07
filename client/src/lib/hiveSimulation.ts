import type { StageStatus, WorkflowStageView } from "../api/client";
import { stageStatusMeta } from "./stageDisplay";

const DESK_POSITIONS: Array<[number, number]> = [
  [18, 44],
  [50, 40],
  [82, 44],
  [18, 76],
  [50, 78],
  [82, 76],
];

const ORCHESTRATOR = { x: 50, y: 10 };

const STATUS_RANK: Record<StageStatus, number> = {
  running: 5,
  blocked: 4,
  awaiting: 3,
  done: 2,
  pending: 1,
  wont_do: 0,
};

export interface HiveAgentDesk {
  id: string;
  name: string;
  init: string;
  x: string;
  y: string;
  color: string;
  status: string;
  stage: string;
  skill: string;
  showTool: boolean;
  active: boolean;
  deskBg: string;
  ring: string;
  avBg: string;
  avFg: string;
  pulsing: boolean;
}

export interface HiveConnectionLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color: string;
  opacity: number;
  width: number;
  dashed: boolean;
  animated: boolean;
}

export interface HiveSimulationModel {
  agents: HiveAgentDesk[];
  lines: HiveConnectionLine[];
  orchestratorActive: boolean;
  idle: boolean;
}

function isHumanAgent(agentId: string): boolean {
  const normalized = agentId.toLowerCase();
  return !normalized || normalized === "—" || normalized.includes("human");
}

function agentInitials(agentId: string): string {
  const base = agentId.replace(/·.*/, "").trim();
  const words = base.split(/[_\s-]+/).filter(Boolean);
  const first = words[0]?.[0] ?? base[0] ?? "?";
  const second = words[1]?.[0] ?? base[1] ?? "";
  return `${first}${second}`.toUpperCase();
}

function formatAgentName(agentId: string): string {
  return agentId
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function isActiveStatus(status: StageStatus): boolean {
  return status === "running" || status === "blocked" || status === "awaiting";
}

export function buildHiveSimulation(stages: WorkflowStageView[]): HiveSimulationModel {
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

  const entries = [...agentMap.values()].slice(0, DESK_POSITIONS.length);

  const agents: HiveAgentDesk[] = entries.map((entry, index) => {
    const [x, y] = DESK_POSITIONS[index] ?? [50, 50];
    const meta = stageStatusMeta(entry.status);
    const active = isActiveStatus(entry.status);

    return {
      id: entry.agentId,
      name: formatAgentName(entry.agentId),
      init: agentInitials(entry.agentId),
      x: `${x}%`,
      y: `${y}%`,
      color: meta.dot,
      status: meta.label,
      stage: entry.stage,
      skill: entry.skill,
      showTool: active && Boolean(entry.skill),
      active,
      deskBg: active ? "var(--bg3)" : "var(--bg2)",
      ring: active ? meta.dot : "var(--bd2)",
      avBg: active ? meta.dot : "var(--bg4)",
      avFg: active ? "var(--onac)" : "var(--txm)",
      pulsing: entry.status === "running",
    };
  });

  const lines: HiveConnectionLine[] = entries.map((entry, index) => {
    const [x2, y2] = DESK_POSITIONS[index] ?? [50, 50];
    const meta = stageStatusMeta(entry.status);
    const active = isActiveStatus(entry.status);

    return {
      x1: ORCHESTRATOR.x,
      y1: ORCHESTRATOR.y,
      x2,
      y2,
      color: active ? meta.dot : "var(--bd2)",
      opacity: active ? 0.85 : 0.35,
      width: active ? 1.6 : 1,
      dashed: active,
      animated: active,
    };
  });

  const orchestratorActive = agents.some((agent) => agent.active);

  return {
    agents,
    lines,
    orchestratorActive,
    idle: agents.length === 0,
  };
}

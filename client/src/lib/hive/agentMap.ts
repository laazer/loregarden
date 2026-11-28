/**
 * Hive's station map: what each of a ticket's agent runs is doing, read from its log.
 *
 * Adapted from alexeyw/lot-map (MIT, hooks/map.ts at 1df9d90): a ring of
 * stations — files, shell, network, memory and the rest — with every tool call
 * sent from the agent to the station it belongs to. lot-map draws one Claude
 * Code session from its hooks; this draws every live run of a ticket, from the
 * `call · <tool> · <subject>` lines the server's log streamer writes
 * (`services/tool_call_line.py`).
 *
 * Pure: log lines in, a model out. Nothing here fetches or draws.
 */

import type { LogLine } from "../../api/runLogTypes";
import { ACTIVE_LEDGER_STATUSES } from "../ledgerStatus";

export type MapStationId =
  | "inbox"
  | "model"
  | "memory"
  | "control"
  | "files"
  | "shell"
  | "network"
  | "browser"
  | "agents"
  | "skills";

/**
 * Every place a call can happen: the ten ring stations, plus the agent's own desk.
 *
 * The desk is where a tool with no station of its own is used — 255 of 27,319 recorded call
 * lines, every one of which used to land silently at Shell. It is deliberately *not* an
 * eleventh ring row: `stationPoint` divides the circle by the number of ring rows, so an
 * eleventh would move all ten existing positions and the CSS geometry pinned to them.
 */
export type MapPlaceId = MapStationId | "desk";

export interface MapPlace {
  id: MapPlaceId;
  label: string;
  /** What lights it, for the place's accessible description. */
  hint: string;
  /** SVG path on a 20-unit box centred on 0,0. */
  icon: string;
  /** True for the ten places drawn on the ring; false for the desk, which is the agent's own point. */
  ring: boolean;
}

function ring(x: number, y: number, r: number): string {
  return `M${x - r} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
}

/**
 * One table decides where a call happens. The ring rows come first, in ring order
 * clockwise from the top; the desk is last and off the ring. Icons are lot-map's, where it
 * has one.
 */
export const MAP_PLACES: readonly MapPlace[] = [
  {
    id: "inbox",
    label: "Inbox",
    hint: "questions, permission requests and refusals",
    icon: `${ring(0, -3.6, 3.4)}M-6.5 8c0-4 3-6.2 6.5-6.2s6.5 2.2 6.5 6.2`,
    ring: true,
  },
  {
    id: "model",
    label: "Model",
    hint: "the agent thinking and answering",
    icon: "M-5 -5h10v10h-10zM-2 -8.5v3.5M2 -8.5v3.5M-2 5v3.5M2 5v3.5M-8.5 -2h3.5M-8.5 2h3.5M5 -2h3.5M5 2h3.5",
    ring: true,
  },
  {
    id: "memory",
    label: "Memory",
    hint: "loregarden memory and learnings",
    icon: `M-5 4.5L0 -5L6 3.5M-5 4.5L6 3.5${ring(-5, 4.5, 2.1)}${ring(0, -5, 2.1)}${ring(6, 3.5, 2.1)}`,
    ring: true,
  },
  {
    id: "control",
    label: "Loregarden",
    hint: "control-plane tools: tickets, stages, artifacts",
    icon: "M-7 -7h14v14h-14zM-7 -2h14M-2 -2v9",
    ring: true,
  },
  {
    id: "files",
    label: "Files",
    hint: "reads, edits, searches and file-only shell commands",
    icon: "M-5.5 -8h7.5l3.5 3.5v12.5h-11zM2 -8v3.5h3.5",
    ring: true,
  },
  {
    id: "shell",
    label: "Shell",
    hint: "every other shell command",
    icon: "M-7.5 -5l5 5l-5 5M0 6h7.5",
    ring: true,
  },
  {
    id: "network",
    label: "Network",
    hint: "web fetch and search, gh, git push, other MCP servers",
    icon: `${ring(0, 0, 7.8)}M-7.8 0h15.6M0 -7.8c-4.2 4.2 -4.2 11.4 0 15.6M0 -7.8c4.2 4.2 4.2 11.4 0 15.6`,
    ring: true,
  },
  {
    id: "browser",
    label: "Browser",
    hint: "browser tools",
    icon: "M-8.5 -6.5h17v13h-17zM-8.5 -2.8h17M-6.3 -4.7h.1M-3.8 -4.7h.1",
    ring: true,
  },
  {
    id: "agents",
    label: "Subagents",
    hint: "subagents the run started",
    icon: `M0 -4v3.5M0 -.5L-6 4M0 -.5L6 4${ring(0, -6, 2.1)}${ring(-6.5, 5.5, 2.1)}${ring(6.5, 5.5, 2.1)}`,
    ring: true,
  },
  {
    id: "skills",
    label: "Skills",
    hint: "skills and tool search",
    icon: "M1.5 -8.5L-5.5 1h4.5l-1.5 7.5l7.5 -9.8h-4.5z",
    ring: true,
  },
  {
    id: "desk",
    label: "Desk",
    hint: "tools with no station of their own, used where the agent sits",
    icon: "M-8.5 -1h17v2.5h-17zM-6.5 1.5v6M6.5 1.5v6M-5 -1v-5.5h10v5.5",
    ring: false,
  },
];

/** The ten places on the ring, derived from the one table so their order and count cannot drift. */
export const MAP_STATIONS: readonly MapPlace[] = MAP_PLACES.filter((place) => place.ring);

// ---- classifying one line ------------------------------------------------------------------------

export type LineEvent =
  /** A tool call, at the one place the table gives it. */
  | { kind: "call"; place: MapPlaceId; tool: string; label: string; failed: boolean }
  /** A result line. `failed` is the server's `result · failed` marker, and nothing else. */
  | { kind: "result"; failed: boolean }
  | { kind: "model" }
  /** `ERR`/`FAIL`: about the run, never about a call. */
  | { kind: "error" };

/** The one marker the server writes on a result headline (`tool_call_line.RESULT_FAILED`). */
const RESULT_FAILED = "failed";

const SEP = " · ";

const FILE_TOOLS = new Set(["Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Grep", "Glob", "LS"]);
const NETWORK_TOOLS = new Set(["WebFetch", "WebSearch"]);
const AGENT_TOOLS = new Set(["Task", "Agent", "Workflow"]);
const SKILL_TOOLS = new Set(["Skill", "ToolSearch"]);
const INBOX_TOOLS = new Set(["AskUserQuestion"]);
const MODEL_TOOLS = new Set(["TodoWrite", "ExitPlanMode", "EnterPlanMode"]);

/** Command words that only look at or move files. */
const FILE_COMMANDS = new Set([
  "ls", "cat", "head", "tail", "grep", "rg", "find", "sed", "awk", "wc", "mv", "cp", "mkdir",
  "rm", "touch", "tree", "stat", "diff", "sort", "uniq", "cut", "jq", "fd",
  "git status", "git diff", "git log", "git show", "git add", "git mv", "git rm", "git grep", "git blame",
]);
/** Command words that leave the machine. */
const NETWORK_COMMANDS = new Set(["curl", "wget", "ssh", "scp", "rsync", "git push", "git pull", "git fetch", "git clone"]);

const BROWSER_SERVERS = new Set(["Claude_Browser", "claude-in-chrome", "playwright", "puppeteer"]);
const MEMORY_TOOL_WORDS = ["memory", "learning"];

function words(name: string): string {
  return name.replace(/_/g, " ");
}

/** A Bash subject is `<head>` or `<head>: <description>` (see `tool_call_line.command_head`). */
function classifyShell(subject: string): { place: MapPlaceId; label: string } {
  const colon = subject.indexOf(": ");
  const head = colon >= 0 ? subject.slice(0, colon) : subject;
  const label = colon >= 0 ? subject.slice(colon + 2) : subject;
  const first = head.split(" ")[0] ?? "";
  if (NETWORK_COMMANDS.has(head) || NETWORK_COMMANDS.has(first) || first === "gh") {
    return { place: "network", label: label || head };
  }
  if (FILE_COMMANDS.has(head) || FILE_COMMANDS.has(first)) return { place: "files", label: label || head };
  return { place: "shell", label: label || head || "shell" };
}

function classifyMcp(tool: string): { place: MapPlaceId; label: string } | null {
  const match = /^mcp__(.+?)__(.+)$/.exec(tool);
  if (!match) return null;
  const [, server, name] = match;
  if (server === "loregarden") {
    const bare = name.replace(/^loregarden_/, "");
    const place = MEMORY_TOOL_WORDS.some((w) => bare.includes(w)) ? "memory" : "control";
    return { place, label: words(bare) };
  }
  if (BROWSER_SERVERS.has(server)) return { place: "browser", label: words(name) };
  return { place: "network", label: `${server}: ${words(name)}` };
}

/**
 * Where one call happens, and what to call it.
 *
 * A tool the table does not know goes to the **desk**, under its own name. It used to fall
 * through to `shell`, which was a silent lie about both the place and the tool: 255 recorded
 * call lines across seven tool names, every one of them shown as a shell command.
 */
export function classifyTool(tool: string, subject: string): { place: MapPlaceId; label: string } {
  if (tool === "Bash" || tool === "shell") return classifyShell(subject);
  const mcp = classifyMcp(tool);
  if (mcp) return mcp;
  const label = subject || tool;
  if (FILE_TOOLS.has(tool)) return { place: "files", label };
  if (NETWORK_TOOLS.has(tool)) return { place: "network", label };
  if (AGENT_TOOLS.has(tool)) return { place: "agents", label };
  if (SKILL_TOOLS.has(tool)) return { place: "skills", label };
  if (INBOX_TOOLS.has(tool)) return { place: "inbox", label: "question for you" };
  if (MODEL_TOOLS.has(tool)) return { place: "model", label: words(tool) };
  // At the desk the tool's own name is the label: there is no subject convention to read.
  return { place: "desk", label: tool };
}

/**
 * What one log line means on the map, or null when it means nothing there.
 *
 * `Auto-approved …` lines are skipped: the call line beside them already names
 * the tool, and counting both would double every permission-bridged call.
 */
export function classifyLine(line: LogLine): LineEvent | null {
  const { tag, text } = line;
  if (tag === "THINK" || tag === "OUT") return { kind: "model" };
  if (tag === "ERR" || tag === "FAIL") return { kind: "error" };
  if (tag !== "TOOL") return null;
  const headline = text.split("\n", 1)[0] ?? "";
  if (headline === "result") return { kind: "result", failed: false };
  if (headline.startsWith(`result${SEP}`)) {
    // Only the one marker means failure. Any other suffix anyone appends later — a truncation
    // note, a size, a duration — is still a result, and so is "Error" in the body (22,147 live
    // result bodies carry it as ordinary content).
    return { kind: "result", failed: headline.slice(`result${SEP}`.length) === RESULT_FAILED };
  }
  if (headline.startsWith(`call${SEP}`)) {
    const rest = headline.slice(`call${SEP}`.length);
    const at = rest.indexOf(SEP);
    const tool = at >= 0 ? rest.slice(0, at) : rest;
    const subject = at >= 0 ? rest.slice(at + SEP.length) : "";
    return { kind: "call", ...classifyTool(tool, subject), tool, failed: false };
  }
  if (headline.startsWith("Denied")) {
    return { kind: "call", place: "inbox", tool: "permission", label: "refused", failed: true };
  }
  if (headline.startsWith("Permission requested") || headline === "Agent asked clarifying questions") {
    return { kind: "call", place: "inbox", tool: "permission", label: "asked you", failed: false };
  }
  // Codex logs a shell call as `$ <command>`; OpenCode logs the bare tool name.
  if (headline.startsWith("$ ")) {
    const command = headline.slice(2).split(SEP)[0] ?? "";
    const head = command.trim().split(/\s+/).slice(0, 2);
    const twoWord = head.join(" ");
    const subject = FILE_COMMANDS.has(twoWord) || NETWORK_COMMANDS.has(twoWord) ? twoWord : (head[0] ?? "");
    return { kind: "call", ...classifyShell(subject), tool: "shell", failed: false };
  }
  if (headline.startsWith("Auto-approved")) return null;
  if (/^[A-Za-z_][\w.-]*$/.test(headline)) {
    return { kind: "call", ...classifyTool(headline, ""), tool: headline, failed: false };
  }
  return null;
}

// ---- the model -----------------------------------------------------------------------------------

export interface MapRunInput {
  runId: string;
  runCode: string;
  agentId: string;
  skillName: string;
  stageKey: string;
  status: string;
  lines: readonly LogLine[];
}

/**
 * What the log says about one call, and nothing it does not say.
 *
 * `done` needs a clean `result` line. `failed` needs a `result · failed` one. A call still open
 * on an active run is `running`; one superseded by the next call with no result of its own, or
 * left open by a run that has ended, is `unknown` — never `done`, and not forever `running`.
 */
export type MapCallState = "running" | "done" | "failed" | "unknown";

export interface MapCall {
  /** Stable across polls while the line stays in the log's 1000-line tail. */
  id: string;
  runId: string;
  place: MapPlaceId;
  tool: string;
  label: string;
  time: string;
  state: MapCallState;
}

export interface MapAgent {
  runId: string;
  runCode: string;
  name: string;
  stageKey: string;
  status: string;
  active: boolean;
  /** The call in flight, or the run's last one. */
  current: MapCall | null;
  calls: number;
  failures: number;
}

export interface MapPlaceState {
  place: MapPlace;
  /** Calls made to it, over every run on the map. Drives the worn spoke. */
  uses: number;
  running: number;
  failures: number;
  /** Uses per run, for drawing a spoke from each agent that came here. */
  usesByRun: Record<string, number>;
}

export interface AgentMapModel {
  agents: MapAgent[];
  /** All eleven places, the desk included, so a desk call is counted and not dropped. */
  places: MapPlaceState[];
  anyActive: boolean;
}

export const FEED_ROWS = 16;

export function agentDisplayName(agentId: string): string {
  return words(agentId);
}

/**
 * One run's calls, in the order its log wrote them.
 *
 * Nothing here infers a state the log does not state. In particular a run-level `ERR`/`FAIL`
 * line marks no call: those are about the run (951 `ERR` + 312 `FAIL` of 344,888 lines, and in
 * run `fcc2a8a7` only at seq 178-180, after 82 clean calls), and blaming the last call for them
 * accused whichever tool the run happened to touch before it died.
 */
export function runCalls(run: Pick<MapRunInput, "runId" | "agentId" | "lines">, active: boolean): MapCall[] {
  const calls: MapCall[] = [];
  const seen = new Map<string, number>();
  // A holder, not a `let`: TypeScript cannot see the closure below assign it.
  const cursor: { open: MapCall | null } = { open: null };
  let inModel = false;
  const make = (
    event: { place: MapPlaceId; tool: string; label: string },
    time: string,
    state: MapCallState,
  ): MapCall => {
    const key = `${run.runId}|${time}|${event.place}|${event.tool}|${event.label}`;
    const n = seen.get(key) ?? 0;
    seen.set(key, n + 1);
    const call: MapCall = {
      id: `${key}|${n}`,
      runId: run.runId,
      place: event.place,
      tool: event.tool,
      label: event.label,
      time,
      state,
    };
    calls.push(call);
    return call;
  };
  run.lines.forEach((line) => {
    const event = classifyLine(line);
    if (event === null) return;
    if (event.kind === "model") {
      // A run of THINK/OUT lines is one model step, not one per streamed chunk. A model step is
      // not a call: it has no result line of its own, so it is never `done`, and it leaves the
      // open call alone rather than closing it.
      if (!inModel) make({ place: "model", tool: "model", label: "thinking" }, line.time, "unknown");
      inModel = true;
      return;
    }
    inModel = false;
    if (event.kind === "result") {
      if (cursor.open) cursor.open.state = event.failed ? "failed" : "done";
      cursor.open = null;
    } else if (event.kind === "call") {
      // The call it supersedes did not succeed; the log simply never said what became of it.
      if (cursor.open) cursor.open.state = "unknown";
      const call = make(event, line.time, event.failed ? "failed" : "running");
      cursor.open = event.failed ? null : call;
    }
  });
  // A run that has ended has nothing in flight, and did not finish what it was holding.
  if (!active && cursor.open) cursor.open.state = "unknown";
  return calls;
}

export function buildAgentMap(runs: readonly MapRunInput[]): AgentMapModel {
  const places: MapPlaceState[] = MAP_PLACES.map((place) => ({
    place,
    uses: 0,
    running: 0,
    failures: 0,
    usesByRun: {},
  }));
  const byId = new Map(places.map((p) => [p.place.id, p]));
  const agents: MapAgent[] = [];

  runs.forEach((run) => {
    const active = ACTIVE_LEDGER_STATUSES.has(run.status.toLowerCase());
    const calls = runCalls(run, active);
    for (const call of calls) {
      // Every place the classifier can name is in this table, the desk included, so nothing
      // here can silently drop a call.
      const state = byId.get(call.place);
      if (!state) continue;
      state.uses += 1;
      state.usesByRun[run.runId] = (state.usesByRun[run.runId] ?? 0) + 1;
      if (call.state === "running") state.running += 1;
      if (call.state === "failed") state.failures += 1;
    }
    agents.push({
      runId: run.runId,
      runCode: run.runCode,
      name: agentDisplayName(run.agentId),
      stageKey: run.stageKey,
      status: run.status,
      active,
      current: calls[calls.length - 1] ?? null,
      calls: calls.length,
      failures: calls.filter((c) => c.state === "failed").length,
    });
  });

  // Only what a component reads: the run list, the place counts, and whether anything is at
  // work. Collapsing consecutive calls into acts is `buildToolActs`'s job and only its job
  // (S10) — a second rule here folded `done` pairs alone and disagreed with it on an
  // `unknown`-state run.
  return { agents, places, anyActive: agents.some((a) => a.active) };
}

// ---- geometry ------------------------------------------------------------------------------------

export const MAP_VIEW = { size: 400, centre: 200, stationRadius: 158 } as const;

export interface Point {
  x: number;
  y: number;
}

export function stationPoint(index: number): Point {
  const angle = (index / MAP_STATIONS.length) * Math.PI * 2 - Math.PI / 2;
  return {
    x: MAP_VIEW.centre + Math.cos(angle) * MAP_VIEW.stationRadius,
    y: MAP_VIEW.centre + Math.sin(angle) * MAP_VIEW.stationRadius,
  };
}

/** One agent sits at the centre; several share a small inner ring. */
export function agentPoint(index: number, count: number): Point {
  if (count <= 1) return { x: MAP_VIEW.centre, y: MAP_VIEW.centre };
  const radius = Math.min(64, 22 + count * 8);
  const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
  return {
    x: MAP_VIEW.centre + Math.cos(angle) * radius,
    y: MAP_VIEW.centre + Math.sin(angle) * radius,
  };
}

/** A worn spoke: thicker the more its station was used, flattening out by ~60 calls. */
export function spokeWidth(uses: number): number {
  return uses <= 0 ? 0 : Math.min(5, 0.8 + Math.log2(1 + uses) * 0.7);
}

// ---- which runs to draw ---------------------------------------------------------------------------

export interface MapAttempt {
  runId: string;
  runCode: string;
  agentId: string;
  skillName: string;
  stageKey: string;
  status: string;
}

interface LedgerLike {
  visits: {
    stage_key: string;
    attempts: {
      run_id: string;
      run_code: string;
      agent_id: string;
      skill_name: string;
      status: string;
      started_at: string | null;
      finished_at: string | null;
    }[];
  }[];
}

/** How long a finished lane stays on the map beside siblings still at work. */
export const RECENT_FINISH_MS = 120_000;

/**
 * The runs the map draws: every run still at work, plus any that finished in
 * the last two minutes (so a lane that ends does not vanish mid-glance). With
 * nothing at work, the ticket's most recent run, so the map shows where the
 * last work went rather than an empty ring.
 */
export function selectMapAttempts(ledger: LedgerLike | undefined, nowMs: number): MapAttempt[] {
  if (!ledger) return [];
  const rows = ledger.visits.flatMap((visit) =>
    visit.attempts.map((a) => ({
      attempt: {
        runId: a.run_id,
        runCode: a.run_code,
        agentId: a.agent_id,
        skillName: a.skill_name,
        stageKey: visit.stage_key,
        status: a.status,
      },
      active: ACTIVE_LEDGER_STATUSES.has(a.status.toLowerCase()),
      finishedMs: a.finished_at ? Date.parse(a.finished_at) : Number.NaN,
      startedMs: a.started_at ? Date.parse(a.started_at) : Number.NaN,
    })),
  );
  const picked = rows.filter(
    (r) => r.active || (Number.isFinite(r.finishedMs) && nowMs - r.finishedMs <= RECENT_FINISH_MS),
  );
  if (picked.length > 0 && picked.some((r) => r.active)) return picked.map((r) => r.attempt);
  const latest = [...rows]
    .filter((r) => Number.isFinite(r.startedMs))
    .sort((a, b) => b.startedMs - a.startedMs)[0];
  return latest ? [latest.attempt] : [];
}

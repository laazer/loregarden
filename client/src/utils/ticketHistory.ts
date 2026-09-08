import type { TicketHistoryEvent } from "../api/types";

/** Transition types, in the vocabulary the log stores them under. */
export const HISTORY_LABELS: Record<string, string> = {
  TicketStateChanged: "State",
  StageStarted: "Stage started",
  StageCompleted: "Stage completed",
  StageSkipped: "Stage skipped",
  GateEvaluated: "Gate",
};

/**
 * One line of the history, and whether it should read as trouble.
 *
 * `tone` is not decoration. `unavailable` means the gate could not run — a
 * missing binary, a hung command — which is a fact about the machine, not about
 * the work. Rendering it the same as `failed` is what sent agents off to fix
 * toolchains they could not see (lg-workflow-integrity-450).
 */
export interface HistoryLine {
  id: string;
  at: string;
  text: string;
  tone: "normal" | "failed" | "unavailable";
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

/** The tiers that name an actual fix. Anything else — `none`, or a missing key
 *  on an event recorded before lg-workflow-integrity-683 — names no fix. */
const FIX_TIERS: Record<string, string> = {
  mechanical: " after an automatic fix",
  agent: " after the agent retried",
};

function afterFix(tier: string): string {
  return FIX_TIERS[tier] ?? "";
}

function gateText(event: TicketHistoryEvent): string {
  const stage = str(event.payload.stage_key);
  const outcome = str(event.payload.outcome);
  const tier = str(event.payload.fix_tier);
  const where = stage ? ` ${stage}` : "";
  if (outcome === "unavailable") return `Gate${where} could not run`;
  return `Gate${where} ${outcome}${afterFix(tier)}`;
}

function describe(event: TicketHistoryEvent): string {
  if (event.type === "GateEvaluated") return gateText(event);
  const stage = event.payload.stage_key ?? event.payload.stage;
  const to = event.payload.to ?? event.payload.state ?? event.payload.status;
  const parts = [HISTORY_LABELS[event.type] ?? event.type];
  if (stage) parts.push(str(stage));
  if (to) parts.push(`→ ${str(to)}`);
  return parts.join(" ");
}

function tone(event: TicketHistoryEvent): HistoryLine["tone"] {
  if (event.type !== "GateEvaluated") return "normal";
  const outcome = str(event.payload.outcome);
  if (outcome === "unavailable") return "unavailable";
  return outcome === "failed" ? "failed" : "normal";
}

function isGate(event: TicketHistoryEvent, outcome: string): boolean {
  return event.type === "GateEvaluated" && str(event.payload.outcome) === outcome;
}

/**
 * The history as lines, with a gate failure and the fix that cleared it joined
 * into one.
 *
 * Two adjacent rows saying "Gate implement failed" and "Gate implement passed"
 * are the same event in the reader's head, and splitting them makes a recovery
 * look like an unexplained contradiction. Joined only when the pass names a fix
 * tier and covers the same stage: without that, the pass is a later evaluation
 * that happens to follow, and merging it would invent a causal link the data
 * does not have.
 */
export function historyLines(events: TicketHistoryEvent[]): HistoryLine[] {
  const lines: HistoryLine[] = [];
  for (let i = 0; i < events.length; i += 1) {
    const event = events[i];
    const next = events[i + 1];
    const joins =
      next !== undefined &&
      isGate(event, "failed") &&
      isGate(next, "passed") &&
      // An EXPLICIT fix tier, not merely "not none". Every event recorded before
      // lg-workflow-integrity-683 has no fix_tier at all, and the live log holds
      // real `failed, failed, passed` runs on one stage — joining those would
      // assert a recovery nothing recorded. Caught by checking the rule against
      // real events rather than fixtures.
      str(next.payload.fix_tier) in FIX_TIERS &&
      str(next.payload.stage_key) === str(event.payload.stage_key);

    if (joins) {
      const stage = str(event.payload.stage_key);
      lines.push({
        id: event.id,
        at: event.created_at,
        text: `Gate ${stage} failed, then passed${afterFix(str(next.payload.fix_tier))}`,
        tone: "normal",
      });
      i += 1;
      continue;
    }
    lines.push({ id: event.id, at: event.created_at, text: describe(event), tone: tone(event) });
  }
  return lines;
}

import type { TicketHistoryEvent } from "../api/types";

/** Transition types, in the vocabulary the log stores them under. */
export const HISTORY_LABELS: Record<string, string> = {
  TicketStateChanged: "State",
  StageStarted: "Stage started",
  StageCompleted: "Stage completed",
  StageSkipped: "Stage skipped",
  GateEvaluated: "Gate",
  OrchestratorDecision: "Orchestrator",
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
  /** What the line is about, when it is longer than a line: the block a repair
   *  was sent to, what the repair agent says it changed, why a stage was
   *  requeued. Absent when the event recorded nothing more. */
  detail?: string;
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

/** Who requeued, in words. `actor` is whatever the caller of `requeue_stage`
 *  passed; an actor not listed here is shown as written. */
const REQUEUE_ACTORS: Record<string, string> = {
  mcp: "through loregarden_requeue_ticket",
  workflow: "after a decision was answered",
  orchestrator: "by the orchestrator",
  "ci-autofix": "to fix failing CI",
};

function requeueText(event: TicketHistoryEvent): string {
  const stage = str(event.payload.stage_key);
  const actor = str(event.payload.actor);
  const who = REQUEUE_ACTORS[actor] ?? (actor ? `by ${actor}` : "");
  return [`Requeued${stage ? ` ${stage}` : ""}`, who].filter(Boolean).join(" ");
}

/**
 * The longer text behind a decision line. A requeue's reason is the detail
 * (its line says who); a repair carries the block it was sent to or, once it
 * passed, the repair agent's account of what it fixed.
 *
 * `detail` is read LAST. Of 647 live decision payloads none carry it, 15 carry
 * `block_message` (the longest 3883 characters) and 3 carry `repair_summary`,
 * so reading it earlier would silently demote the block on every one of those
 * 15 rows. A settled handoff carries the whole failure text there, which is how
 * git's merge output reaches the line's disclosure without reaching its text.
 */
function decisionDetail(event: TicketHistoryEvent): string | undefined {
  const decision = str(event.payload.decision);
  const detail =
    decision === "requeued"
      ? str(event.payload.reason)
      : str(event.payload.repair_summary) ||
        str(event.payload.block_message) ||
        str(event.payload.detail);
  return detail || undefined;
}

function decisionText(event: TicketHistoryEvent): string {
  if (str(event.payload.decision) === "requeued") return requeueText(event);
  // The reason is already a sentence written for a person; the rail shows it
  // verbatim rather than reassembling it from the payload's parts. Falls back
  // to the decision kind so an event with no reason still says what it was
  // rather than nothing.
  const reason = str(event.payload.reason);
  if (reason) return `Orchestrator: ${reason}`;
  const kind = str(event.payload.decision).replace(/_/g, " ");
  return `Orchestrator ${kind}`.trim();
}

function describe(event: TicketHistoryEvent): string {
  if (event.type === "GateEvaluated") return gateText(event);
  if (event.type === "OrchestratorDecision") return decisionText(event);
  const stage = event.payload.stage_key ?? event.payload.stage;
  const to = event.payload.to ?? event.payload.state ?? event.payload.status;
  const parts = [HISTORY_LABELS[event.type] ?? event.type];
  if (stage) parts.push(str(stage));
  if (to) parts.push(`→ ${str(to)}`);
  return parts.join(" ");
}

function tone(event: TicketHistoryEvent): HistoryLine["tone"] {
  // A refusal or a settle is the orchestrator declining to let something
  // proceed — worth reading as trouble, the way a failed gate is. An overrule
  // is the orchestrator letting something proceed that a gate would have
  // stopped, which is the opposite and reads normal.
  if (event.type === "OrchestratorDecision") {
    const decision = str(event.payload.decision);
    // The one kind whose tone depends on how it went: the sweep re-enters a
    // workflow nothing was left to finish, and the finish can still block on
    // the landing it retried. It carries the state it reached.
    if (decision === "finished_parked_terminal_stage") {
      return str(event.payload.state) === "done" ? "normal" : "failed";
    }
  return decision === "overruled_stale_gate" ||
    decision === "approved_design_plan" ||
    decision === "approved_legacy_sign_off" ||
    decision === "requeued_after_decision" ||
    decision === "parked_after_decision" ||
    decision === "advanced_after_decision" ||
    decision === "dispatched_repair" ||
    decision === "dispatched_landing_resolver" ||
    decision === "dispatched_ci_fix" ||
    decision === "finished_cleared_landing" ||
    decision === "repaired" ||
    decision === "requeued" ||
    // Waiting on another ticket is not this one failing, and the two ways out
    // of it — landing, or a person starting it anyway — are not either.
    decision === "held_on_prerequisites" ||
    decision === "prerequisites_overridden" ||
    decision === "released_on_prerequisites" ||
    // Splitting is the orchestrator shrinking the work, not trouble; running
    // oversized and unsplit is trouble and stays the "failed" default.
    decision === "split_oversized_ticket"
    ? "normal"
    : "failed";
  }
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
    const detail = event.type === "OrchestratorDecision" ? decisionDetail(event) : undefined;
    lines.push({
      id: event.id,
      at: event.created_at,
      text: describe(event),
      tone: tone(event),
      ...(detail ? { detail } : {}),
    });
  }
  return lines;
}

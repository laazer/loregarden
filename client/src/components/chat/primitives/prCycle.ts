/**
 * Where a branch stands on its way to the base, and the one thing to do next.
 *
 * Three stages in order: the push's pre-push suite, the PR's checks, the merge
 * queue. The first stage that is not finished decides the card's one action:
 * Repair when it failed, Enqueue PR when the PR is green and not queued, and
 * nothing (a disabled button saying what it waits on) otherwise.
 */

import type { MergeQueueEntry, PrCycleView } from "../../../api/mergeQueueApi";
import type { PullRequestStatus } from "../../../api/ticketPullRequestApi";
import type { VineMark } from "../../../lib/vine";
import type { ChipStatus } from "../../../lib/statusTone";
import type { PrCyclePart } from "./types";

export type PrCycleStageKey = "pre_push" | "pr_checks" | "merge_queue";

export interface PrCycleStage {
  key: PrCycleStageKey;
  label: string;
  mark: VineMark;
  detail: string;
}

export type PrCycleAction =
  /** Ask the thread's agent to fix it; `prompt` is the message sent. */
  | { kind: "repair"; prompt: string; hint: string }
  | { kind: "enqueue"; prNumber: number; hint: string }
  /** Nothing to do now: the button is disabled and says why. */
  | { kind: "wait"; label: string; hint: string };

export interface PrCyclePlan {
  stages: PrCycleStage[];
  action: PrCycleAction;
  status: ChipStatus;
}

const LIVE_QUEUE_STATES = new Set<MergeQueueEntry["state"]>(["queued", "staging", "testing", "merging"]);
const STOPPED_QUEUE_STATES = new Set<MergeQueueEntry["state"]>(["failed", "timed_out"]);
/** The tail is what names the cause; a whole suite's output would bury it. */
const LOG_TAIL_LINES = 40;

function tail(text: string): string {
  return text.trim().split("\n").slice(-LOG_TAIL_LINES).join("\n");
}

function fenced(text: string): string {
  const body = tail(text);
  return body ? `\n\n\`\`\`\n${body}\n\`\`\`` : "";
}

function stage(key: PrCycleStageKey, mark: VineMark, detail: string): PrCycleStage {
  const label = { pre_push: "Pre-push checks", pr_checks: "PR checks", merge_queue: "Merge queue" }[key];
  return { key, label, mark, detail };
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function prePushStage(part: PrCyclePart, prOpen: boolean): PrCycleStage {
  switch (part.pre_push) {
    case "passed":
      return stage("pre_push", "leaf", "Passed on push");
    case "failed":
      return stage("pre_push", "bud-blocked", "Failed: the push was refused");
    default:
      // Nothing recorded it. A PR on the branch means some push got through.
      return prOpen
        ? stage("pre_push", "leaf", "Not recorded; the branch was pushed")
        : stage("pre_push", "bud-closed", "No result recorded");
  }
}

function queueDetail(entry: MergeQueueEntry): string {
  switch (entry.state) {
    case "queued":
      return entry.ahead ? `Queued, ${entry.ahead} ahead` : "Queued, next in line";
    case "staging":
      return "Staging onto the base";
    case "testing":
      return "Running required checks on the staged merge";
    case "merging":
      return "Merging";
    case "merged":
      return "Merged";
    case "withdrawn":
      return "Withdrawn";
    default:
      return entry.detail || (entry.state === "timed_out" ? "Timed out" : "Failed");
  }
}

function queueRepairPrompt(part: PrCyclePart, entry: MergeQueueEntry): string {
  const files = entry.conflicted_files.length
    ? `\n\nConflicted files: ${entry.conflicted_files.map((file) => `\`${file}\``).join(", ")}`
    : "";
  return (
    `PR #${entry.pr_number} (\`${part.branch}\`) stopped in the merge queue: ${entry.detail || entry.state}. ` +
    `Fix the cause on the branch and push it.${files}${fenced(entry.log_tail)}`
  );
}

/** The PR's own checks, when it is open and not in (or through) the queue. */
function checksPlan(part: PrCyclePart, pr: PullRequestStatus, queued: PrCycleStage): Omit<PrCyclePlan, "stages"> & {
  checks: PrCycleStage;
  queue: PrCycleStage;
} {
  const failing = pr.checks.filter((check) => check.outcome === "failing");
  const pending = pr.checks.filter((check) => check.outcome === "pending");
  if (pr.has_conflicts) {
    return {
      checks: stage("pr_checks", "bud-blocked", "Conflicts with the base branch"),
      queue: queued,
      status: { tone: "bad", label: "Conflicts" },
      action: {
        kind: "repair",
        hint: "Ask the agent to merge the base in and resolve the conflicts",
        prompt: `PR #${pr.number} (\`${part.branch}\`) conflicts with \`${pr.base}\`. Merge \`${pr.base}\` into the branch, resolve the conflicts, and push.`,
      },
    };
  }
  if (failing.length) {
    const names = failing.map((check) => (check.url ? `- ${check.name}: ${check.url}` : `- ${check.name}`)).join("\n");
    return {
      checks: stage("pr_checks", "bud-blocked", `${plural(failing.length, "check")} failing: ${failing.map((c) => c.name).join(", ")}`),
      queue: queued,
      status: { tone: "bad", label: "Checks failing" },
      action: {
        kind: "repair",
        hint: "Ask the agent to fix the failing checks",
        prompt: `PR #${pr.number} (\`${part.branch}\`) has failing checks. Read their logs, fix the cause on the branch, and push.\n\n${names}`,
      },
    };
  }
  if (pending.length || !pr.checks.length) {
    return {
      checks: stage(
        "pr_checks",
        "bud-open",
        pr.checks.length ? `${pending.length} of ${pr.checks.length} still running` : "Waiting for checks to report",
      ),
      queue: queued,
      status: { tone: "run", label: "Checks running" },
      action: { kind: "wait", label: "Checks running", hint: "Enqueue opens once every check passes" },
    };
  }
  if (pr.is_draft) {
    return {
      checks: stage("pr_checks", "bud-held", `All ${plural(pr.checks.length, "check")} pass; the PR is a draft`),
      queue: queued,
      status: { tone: "you", label: "Draft" },
      action: { kind: "wait", label: "Draft PR", hint: "Mark the PR ready for review to queue it" },
    };
  }
  return {
    checks: stage("pr_checks", "leaf", `All ${plural(pr.checks.length, "check")} pass`),
    queue: queued,
    status: { tone: "you", label: "Ready to queue" },
    action: { kind: "enqueue", prNumber: pr.number, hint: `Queue PR #${pr.number} to land on ${pr.base}` },
  };
}

export function planPrCycle(part: PrCyclePart, view: PrCycleView | undefined): PrCyclePlan {
  const read = view?.pull_request;
  const pr = read?.lookup === "found" ? read.pull_request : null;
  const entry = view?.entry ?? null;
  const prePush = prePushStage(part, Boolean(pr));

  if (part.pre_push === "failed") {
    return {
      stages: [prePush, stage("pr_checks", "bud-closed", "Not reached"), stage("merge_queue", "bud-closed", "Not reached")],
      status: { tone: "bad", label: "Pre-push failed" },
      action: {
        kind: "repair",
        hint: "Ask the agent to fix what the pre-push checks caught, then push again",
        prompt:
          `The push of \`${part.branch}\` failed its pre-push checks. Fix the cause, then push the branch again.` +
          fenced(part.pre_push_detail ?? ""),
      },
    };
  }

  if (!pr) {
    const failed = read?.lookup === "failed";
    return {
      stages: [
        prePush,
        stage("pr_checks", "bud-closed", failed ? "Couldn't ask GitHub" : "No PR open for this branch"),
        stage("merge_queue", "bud-closed", "Not reached"),
      ],
      status: failed ? { tone: "wait", label: "Unknown" } : { tone: "idle", label: "No PR" },
      action: failed
        ? { kind: "wait", label: "Can't read PR", hint: "The card retries when you reopen the thread" }
        : { kind: "wait", label: "No PR yet", hint: "Open a PR from this branch to run its checks" },
    };
  }

  const checksDone = stage("pr_checks", "leaf", `PR #${pr.number}`);
  if (pr.state === "merged" || entry?.state === "merged") {
    return {
      stages: [prePush, checksDone, stage("merge_queue", "leaf", `Merged into ${pr.base}`)],
      status: { tone: "done", label: "Merged" },
      action: { kind: "wait", label: "Merged", hint: "Nothing left to do" },
    };
  }
  if (pr.state === "closed") {
    return {
      stages: [prePush, stage("pr_checks", "pruned", `PR #${pr.number} closed without merging`), stage("merge_queue", "pruned", "Not reached")],
      status: { tone: "idle", label: "Closed" },
      action: { kind: "wait", label: "PR closed", hint: "Reopen the PR to continue" },
    };
  }
  if (entry && LIVE_QUEUE_STATES.has(entry.state)) {
    return {
      stages: [prePush, checksDone, stage("merge_queue", "bud-open", queueDetail(entry))],
      status: { tone: "run", label: "In queue" },
      action: { kind: "wait", label: "In merge queue", hint: "The queue lands it once its checks pass on the base" },
    };
  }
  if (entry && STOPPED_QUEUE_STATES.has(entry.state)) {
    return {
      stages: [prePush, checksDone, stage("merge_queue", "bud-blocked", queueDetail(entry))],
      status: { tone: "bad", label: "Queue failed" },
      action: { kind: "repair", hint: "Ask the agent to fix why the queue stopped it", prompt: queueRepairPrompt(part, entry) },
    };
  }

  const notQueued = stage("merge_queue", "bud-closed", entry ? queueDetail(entry) : "Not queued");
  const { checks, queue, status, action } = checksPlan(part, pr, notQueued);
  return { stages: [prePush, checks, queue], status, action };
}

/** Whether the card should keep re-reading: something is moving on its own. */
export function prCycleIsMoving(plan: PrCyclePlan): boolean {
  return plan.stages.some((s) => s.mark === "bud-open");
}

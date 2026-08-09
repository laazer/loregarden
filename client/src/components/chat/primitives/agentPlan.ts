/**
 * Agent execution plans across a thread.
 *
 * A plan is a living card, not a log entry: the agent re-emits it as steps
 * complete, and every re-emission is a fresh `todo_list` part on a new message.
 * Rendering each one leaves a column of stale plans whose Run buttons all still
 * look live. The thread therefore keeps only the newest card per plan identity
 * and drops the ones it superseded.
 *
 * Identity is the agent-supplied `plan_id`; without one we fall back to the
 * card's title, which is what the model varies least between turns.
 */

import type { ChatMessageView } from "../chatUtils";
import type { ChatPart, TodoListPart, UnknownPart } from "./types";

/** Prefix of the user message the Run button posts. Mirrors the server constant. */
export const AGENT_PLAN_EXECUTE_PREFIX = "Execute this agent execution plan now.";

export const DEFAULT_AGENT_PLAN_TITLE = "Agent execution plan";

function isAgentTodoList(part: ChatPart | UnknownPart): part is TodoListPart {
  return part.primitive === "todo_list" && (part as TodoListPart).owner !== "user";
}

/** Stable key for an agent plan, or null when the part is not one. */
export function agentPlanIdentity(part: ChatPart | UnknownPart): string | null {
  if (!isAgentTodoList(part)) return null;
  const planId = part.plan_id?.trim();
  if (planId) return `id:${planId.toLowerCase()}`;
  const title = part.title?.trim() || DEFAULT_AGENT_PLAN_TITLE;
  return `title:${title.toLowerCase()}`;
}

export function agentPlanPartKey(messageId: string, partIndex: number): string {
  return `${messageId}#${partIndex}`;
}

/**
 * Keys of plan cards a later card replaced — every occurrence of an identity
 * except its last one, in thread order.
 */
export function supersededAgentPlanKeys(messages: ChatMessageView[]): Set<string> {
  const latest = new Map<string, string>();
  const seen: Array<{ identity: string; key: string }> = [];

  for (const message of messages) {
    (message.parts ?? []).forEach((part, index) => {
      const identity = agentPlanIdentity(part);
      if (!identity) return;
      const key = agentPlanPartKey(message.id, index);
      latest.set(identity, key);
      seen.push({ identity, key });
    });
  }

  const superseded = new Set<string>();
  for (const entry of seen) {
    if (latest.get(entry.identity) !== entry.key) superseded.add(entry.key);
  }
  return superseded;
}

/**
 * A short label for the Run message, or null when this is ordinary user text.
 *
 * The Run button posts the whole plan so the agent has the steps verbatim even
 * after history clipping. Showing that payload as a user bubble reads as the
 * operator having pasted the plan back at Baxter, so the thread renders the
 * intent instead.
 */
export function agentPlanRunSummary(content: string): { title: string; steps: number } | null {
  const text = (content ?? "").trimStart();
  if (!text.startsWith(AGENT_PLAN_EXECUTE_PREFIX)) return null;
  const lines = text.split("\n");
  const planLine = lines.find((line) => line.trimStart().startsWith("Plan: "));
  const title = planLine?.trimStart().slice("Plan: ".length).trim() || DEFAULT_AGENT_PLAN_TITLE;
  const steps = lines.filter((line) => /^\s*-\s*\[ \]\s+/.test(line)).length;
  return { title, steps };
}

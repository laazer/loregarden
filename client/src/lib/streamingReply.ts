/**
 * A reply that is still being written, cut into the pieces that are finished
 * and the one that is not.
 *
 * Rendering the whole stream as markdown makes half a table or half a fence
 * rearrange itself on every frame; rendering it all as plain text shows a card
 * the agent is emitting as a page of raw JSON. Neither is readable. So the
 * stream is chunked at block boundaries: every block the agent has finished
 * (a paragraph followed by a blank line, a closed code fence) renders as it
 * will settle, a closed `loregarden` card fence becomes a card, and only the
 * block still being written stays plain text.
 */

import { agentPlanIdentity } from "../components/chat/primitives/agentPlan";
import type { TodoListPart } from "../components/chat/primitives/types";

export type StreamingChunk =
  /** Finished blocks, safe to render as markdown. */
  | { kind: "markdown"; text: string }
  /** A closed card fence. `plan` is the server-validated plan when there is one. */
  | { kind: "card"; primitive: string; plan: TodoListPart | null }
  /** A card fence still being written; `primitive` once its kind has streamed. */
  | { kind: "card-forming"; primitive: string | null }
  /** The block still being written, as plain text. */
  | { kind: "tail"; text: string };

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w-]*)/;
const PRIMITIVE_KIND = /"primitive"\s*:\s*"([\w-]+)"/;

interface OpenFence {
  marker: string;
  isCard: boolean;
  start: number;
}

function closesFence(line: string, fence: OpenFence): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith(fence.marker) && /^(`+|~+)$/.test(trimmed);
}

const JSON_STRING_FIELD = (field: string) => new RegExp(`"${field}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"`);
const PLAN_ID = JSON_STRING_FIELD("plan_id");
const TITLE = JSON_STRING_FIELD("title");

/**
 * The validated plan this card fence carries, matched by identity.
 *
 * Read from the fence's `plan_id`/`title` strings rather than `JSON.parse`: the
 * server repairs near-miss JSON an agent writes (a dropped closing brace is
 * common), so a strict parse here would miss cards the server accepted. The
 * server stays the one validator; this only finds which of its plans is meant.
 */
function planForFence(body: string, plans: TodoListPart[]): TodoListPart | null {
  if (!plans.length) return null;
  const identity = agentPlanIdentity({
    primitive: "todo_list",
    owner: "agent",
    plan_id: PLAN_ID.exec(body)?.[1] ?? null,
    title: TITLE.exec(body)?.[1] ?? null,
    items: [],
  } as TodoListPart);
  return plans.find((plan) => agentPlanIdentity(plan) === identity) ?? null;
}

export function chunkStreamingReply(answer: string, plans: TodoListPart[]): StreamingChunk[] {
  const lines = answer.replace(/\r\n/g, "\n").split("\n");
  const chunks: StreamingChunk[] = [];
  let buffer: string[] = [];
  // Index into `buffer` just past the last finished block.
  let settledUpTo = 0;
  let fence: OpenFence | null = null;

  const flushMarkdown = (upTo: number) => {
    const text = buffer.slice(0, upTo).join("\n").trim();
    if (text) chunks.push({ kind: "markdown", text });
    buffer = buffer.slice(upTo);
    settledUpTo = 0;
  };

  for (const line of lines) {
    if (fence) {
      if (closesFence(line, fence)) {
        if (fence.isCard) {
          const body = buffer.slice(fence.start + 1).join("\n");
          buffer = buffer.slice(0, fence.start);
          flushMarkdown(buffer.length);
          chunks.push({
            kind: "card",
            primitive: PRIMITIVE_KIND.exec(body)?.[1] ?? "unknown",
            plan: planForFence(body, plans),
          });
        } else {
          buffer.push(line);
          settledUpTo = buffer.length;
        }
        fence = null;
      } else {
        buffer.push(line);
      }
      continue;
    }

    const open = FENCE.exec(line);
    if (open) {
      // A fence starts a new block, so whatever precedes it is finished.
      settledUpTo = buffer.length;
      fence = { marker: open[1], isCard: open[2] === "loregarden", start: buffer.length };
      buffer.push(line);
      continue;
    }

    buffer.push(line);
    if (!line.trim()) settledUpTo = buffer.length;
  }

  if (fence?.isCard) {
    const body = buffer.slice(fence.start + 1).join("\n");
    buffer = buffer.slice(0, fence.start);
    flushMarkdown(buffer.length);
    chunks.push({ kind: "card-forming", primitive: PRIMITIVE_KIND.exec(body)?.[1] ?? null });
    return chunks;
  }

  flushMarkdown(fence ? fence.start : settledUpTo);
  const tail = buffer.join("\n").replace(/^\n+/, "");
  if (tail.trim()) chunks.push({ kind: "tail", text: tail });
  return chunks;
}

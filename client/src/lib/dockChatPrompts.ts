import type { ChatMessageView } from "../components/chat/chatUtils";

/**
 * Copy for the global action bar's composer and its quick prompts.
 *
 * Shared because the bar sends them and the expanded panel offers the same
 * openers above an empty thread — one list, or the two drift apart.
 */
export const COMPOSER_PLACEHOLDER: Record<string, string> = {
  "ticket-triage": "Message about this ticket…",
  "branch-triage": "Message about this branch…",
  "baxter-home": "Ask Baxter anything about this workspace…",
};

/**
 * Openers for the questions this dock is usually opened to ask.
 *
 * Prompt shortcuts, not suggestions: nothing infers them from the ticket.
 */
export const TRY_ASKING: Record<string, string[]> = {
  "ticket-triage": [
    "What is blocking this ticket?",
    "Summarise what the last run changed",
    "Why did the last stage fail?",
  ],
  "branch-triage": [
    "What changed on this branch?",
    "Is this branch safe to delete?",
    "commit and push",
  ],
  "baxter-home": [
    "What should I work on next?",
    "What is blocked right now?",
    "Summarise today's runs",
  ],
};

/** Ship the work in front of you: the one opener that is an action, not a question. */
export const SHIP_PROMPT = "Commit, push, and open a PR";

const DEFAULT_BRANCHES = new Set(["main", "master"]);

/**
 * The openers for a conversation, with the shipping action first when the work
 * sits on a branch of its own.
 *
 * Withheld on the default branch: there is nothing to open a pull request
 * against from main, so the action would only ever fail.
 */
export function quickPrompts(kind: string, branch: string | null | undefined): string[] {
  const base = TRY_ASKING[kind] ?? [];
  if (!branch || DEFAULT_BRANCHES.has(branch)) return base;
  return [SHIP_PROMPT, ...base];
}

/** The bar has room for a couple of openers; the panel shows them all. */
export const DOCK_QUICK_PROMPT_LIMIT = 2;

/** Replies to a reply that ended on a question — the two answers it is asking for. */
export const CONFIRM_PROMPT = "Yes, go ahead";
export const DECLINE_PROMPT = "No, leave it as is";

/** Asked after any answer; it fills the row when nothing more specific applies. */
export const ELABORATE_PROMPT = "Tell me more";

/** How many reply-specific prompts a turn can contribute before the generic ones. */
const REPLY_PROMPT_LIMIT = 3;

/** What a card in the reply most often leads the operator to ask next. */
const PROMPT_FOR_CARD: Partial<Record<string, string>> = {
  edit: "Apply this change",
  commit: "Push it and open a PR",
  terminal: "Explain this output",
  ticket: "What's the next step on this ticket?",
  ticket_workflow: "What's the next step on this ticket?",
  parent_ticket: "Which child ticket should go first?",
  ticket_list: "Which of these should I do first?",
  status_column: "Which of these should I do first?",
  kanban: "Which of these should I do first?",
  filterable_kanban: "Which of these should I do first?",
  todo_list: "Start on the first item",
  branch_history: "Summarise these commits",
  gate: "Explain this gate",
};

const FAILURE_WORDS =
  /\b(fail(s|ed|ing|ure)?|error(s|ed)?|exception|traceback|broken|crash(ed|es)?)\b/i;

function replyText(message: ChatMessageView): string {
  const fromParts = (message.parts ?? [])
    .filter((part) => part.primitive === "text")
    .map((part) => String((part as { content?: unknown }).content ?? ""));
  return [message.content, ...fromParts].join("\n").trim();
}

/** The last thing said in prose — a question in a code block is not one put to the operator. */
function endsOnQuestion(text: string): boolean {
  const prose = text.replace(/```[\s\S]*?```/g, "").trim();
  return prose.endsWith("?");
}

/**
 * Prompts that answer the latest reply specifically: the question it ended on,
 * the cards it drew, the failure it reported.
 *
 * Empty when there is no reply yet, or nothing in it calls for a particular
 * answer — the caller decides what fills the row then.
 */
export function replyPrompts(messages: readonly ChatMessageView[]): string[] {
  const last = [...messages].reverse().find((m) => m.role === "assistant");
  if (!last) return [];
  const text = replyText(last);
  const prompts: string[] = [];
  if (endsOnQuestion(text)) prompts.push(CONFIRM_PROMPT, DECLINE_PROMPT);
  for (const part of last.parts ?? []) {
    const prompt = PROMPT_FOR_CARD[part.primitive];
    if (prompt) prompts.push(prompt);
  }
  if (FAILURE_WORDS.test(text)) prompts.push("How do we fix it?");
  return unique(prompts).slice(0, REPLY_PROMPT_LIMIT);
}

/**
 * The quick responses for a conversation at its current point.
 *
 * An empty thread gets the openers. After that, the latest reply's own
 * follow-ups lead, then shipping (on a branch of its own), then the openers not
 * yet asked — a prompt the operator already sent is never offered back.
 */
export function followUpPrompts(
  kind: string,
  branch: string | null | undefined,
  messages: readonly ChatMessageView[],
): string[] {
  const openers = quickPrompts(kind, branch);
  if (messages.length === 0) return openers;
  return withReplyPrompts([...openers, ELABORATE_PROMPT], messages);
}

/**
 * `candidates` behind the latest reply's own follow-ups, minus anything the
 * operator already sent — so a host with its own openers orders them the same
 * way the bar does.
 */
export function withReplyPrompts(
  candidates: readonly string[],
  messages: readonly ChatMessageView[],
): string[] {
  const asked = new Set(
    messages.filter((m) => m.role === "user").map((m) => normalise(m.content)),
  );
  return unique([...replyPrompts(messages), ...candidates]).filter(
    (prompt) => !asked.has(normalise(prompt)),
  );
}

function normalise(prompt: string): string {
  return prompt.trim().toLowerCase();
}

function unique(prompts: string[]): string[] {
  return [...new Set(prompts)];
}

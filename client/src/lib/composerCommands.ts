/**
 * `/` commands and `@` references for the chat composers.
 *
 * Pure text handling only — no React, no requests. What a draft *means* is
 * decided here so every composer agrees on it, and so the rules can be tested
 * without mounting an input.
 *
 * Two triggers, deliberately asymmetric:
 *
 * - `/` is a command on the *message*, so it is only a trigger at the very
 *   start of the draft. A slash anywhere else is a path, a fraction, or prose.
 * - `@` is a reference *inside* the message, so it triggers anywhere a word
 *   can start.
 */

/** A command the `/` menu offers. Built-ins act; skills change how a turn runs. */
export interface ComposerCommand {
  name: string;
  /** Shorter spellings that resolve to the same command, e.g. `q` for `queue`. */
  aliases: string[];
  summary: string;
  kind: "builtin" | "skill";
}

export const BUILTIN_COMMANDS: readonly ComposerCommand[] = [
  {
    name: "queue",
    aliases: ["q"],
    summary: "Send this the moment the current reply lands",
    kind: "builtin",
  },
  {
    name: "note",
    aliases: [],
    summary: "Keep this as a post-it you can send later, here or in a new chat",
    kind: "builtin",
  },
] as const;

export function skillCommands(skills: readonly string[] | undefined): ComposerCommand[] {
  return (skills ?? []).map((skill) => ({
    name: skill,
    aliases: [],
    summary: "Run this turn with the skill in front of it",
    kind: "skill" as const,
  }));
}

/** An in-progress `/` or `@` token under the caret. */
export interface ComposerTrigger {
  kind: "slash" | "mention";
  /** What has been typed after the sigil, up to the caret. */
  query: string;
  /** Index of the sigil itself. */
  start: number;
  /** Index just past the caret's token — where a completion should end. */
  end: number;
}

function isWordBoundary(char: string | undefined): boolean {
  return char === undefined || /\s/.test(char);
}

/**
 * The trigger the caret currently sits in, or null.
 *
 * `end` is the caret rather than the end of the surrounding token on purpose:
 * completing mid-word replaces what was typed and leaves the tail alone, which
 * is what every editor's completion does.
 */
export function activeTrigger(value: string, caret: number): ComposerTrigger | null {
  const position = Math.max(0, Math.min(caret, value.length));

  if (value.startsWith("/")) {
    const firstSpace = value.search(/\s/);
    const tokenEnd = firstSpace === -1 ? value.length : firstSpace;
    if (position <= tokenEnd) {
      return { kind: "slash", query: value.slice(1, position), start: 0, end: position };
    }
  }

  const before = value.slice(0, position);
  const at = before.lastIndexOf("@");
  if (at === -1) return null;
  const query = before.slice(at + 1);
  // A whitespace run ends the reference: `@foo bar` is a reference plus a word.
  if (/\s/.test(query)) return null;
  if (!isWordBoundary(before[at - 1])) return null;
  return { kind: "mention", query, start: at, end: position };
}

/** Rank commands against what has been typed, exact and prefix hits first. */
export function matchCommands(
  commands: readonly ComposerCommand[],
  query: string,
): ComposerCommand[] {
  const text = query.trim().toLowerCase();
  if (!text) return [...commands];
  const scored: Array<{ score: number; command: ComposerCommand }> = [];
  for (const command of commands) {
    const names = [command.name, ...command.aliases].map((name) => name.toLowerCase());
    let best: number | null = null;
    for (const name of names) {
      if (name === text) best = Math.min(best ?? Infinity, 0);
      else if (name.startsWith(text)) best = Math.min(best ?? Infinity, 1);
      else if (name.includes(text)) best = Math.min(best ?? Infinity, 2);
    }
    if (best !== null) scored.push({ score: best, command });
  }
  scored.sort((a, b) => a.score - b.score || a.command.name.localeCompare(b.command.name));
  return scored.map((entry) => entry.command);
}

export interface CompletionResult {
  value: string;
  caret: number;
}

/**
 * Replace the trigger's text with `insert`, leaving the rest of the draft alone.
 *
 * `trailing` is what follows the inserted token: a space closes the token, and
 * a `/` on a directory leaves the reference open so the next keystroke keeps
 * narrowing inside it.
 */
export function applyCompletion(
  value: string,
  trigger: ComposerTrigger,
  insert: string,
  trailing: string,
): CompletionResult {
  const tail = value.slice(trigger.end);
  // Completing mid-message would otherwise leave a double space: the separator
  // this adds plus the one already sitting after the word it replaced.
  const separator = trailing === " " && /^\s/.test(tail) ? "" : trailing;
  const head = value.slice(0, trigger.start) + insert + separator;
  return { value: head + tail, caret: head.length };
}

export interface ParsedDraft {
  /** The leading `/name`, lowercased and without the slash, or "". */
  command: string;
  /** The draft with the leading command token removed. */
  body: string;
}

/**
 * Split a leading `/command` off the draft.
 *
 * Only the first token, and only at the start — the same rule `activeTrigger`
 * uses, so what the menu offered and what the send interprets never disagree.
 */
export function parseDraft(value: string): ParsedDraft {
  if (!value.startsWith("/")) return { command: "", body: value };
  const match = /^\/(\S*)\s*([\s\S]*)$/.exec(value);
  if (!match) return { command: "", body: value };
  return { command: match[1].toLowerCase(), body: match[2] };
}

/** Resolve a typed command token to a known command, following aliases. */
export function resolveCommand(
  commands: readonly ComposerCommand[],
  token: string,
): ComposerCommand | null {
  const text = token.trim().toLowerCase();
  if (!text) return null;
  return (
    commands.find(
      (command) =>
        command.name.toLowerCase() === text ||
        command.aliases.some((alias) => alias.toLowerCase() === text),
    ) ?? null
  );
}

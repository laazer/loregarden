/**
 * Pure helpers behind the Gate Studio editor: naming a check, finding the
 * placeholders it uses, and expanding it the way the server's
 * `format_gate_command` will.
 */

/** What each placeholder means, for the chip tooltips. The set itself comes from the server. */
export const PLACEHOLDER_HELP: Record<string, string> = {
  ticket_id: "The ticket's internal id",
  external_id: "The ticket's human id, e.g. M12-01",
  transition: "The edge being crossed, e.g. implement_to_verify",
  from_stage: "The stage that just finished",
  to_stage: "The stage the ticket is handing off to",
  workspace_root: "The ticket's worktree — where the stage's edits are",
  workspace_slug: "This workspace's slug",
  loregarden_root: "Loregarden's own checkout, for checks that ship with it",
};

const PLACEHOLDER_RE = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

/** Every `{name}` a command references, in order, without duplicates. */
export function placeholdersIn(command: string): string[] {
  const seen = new Set<string>();
  for (const match of command.matchAll(PLACEHOLDER_RE)) seen.add(match[1]);
  return [...seen];
}

/** Placeholders the server does not know — it runs such a command verbatim, braces and all. */
export function unknownPlaceholders(command: string, known: Record<string, string>): string[] {
  return placeholdersIn(command).filter((name) => !(name in known));
}

/** The command as the gate will run it, with known placeholders filled from `values`. */
export function expandGateCommand(command: string, values: Record<string, string>): string {
  return command.replace(PLACEHOLDER_RE, (whole, name: string) =>
    name in values ? values[name] : whole,
  );
}

/** True when a quote is left open — the server's shlex.split rejects the whole command. */
export function hasUnbalancedQuotes(command: string): boolean {
  let quote: string | null = null;
  for (let i = 0; i < command.length; i += 1) {
    const ch = command[i];
    if (ch === "\\" && quote !== "'") {
      i += 1;
    } else if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    }
  }
  return quote !== null;
}

/** Wrappers that only launch the real check, so they make a poor name for it. */
const LAUNCHERS = new Set(["bash", "sh", "node", "python", "python3", "npx", "uv", "run"]);
const SCRIPT_RE = /\.(py|cjs|mjs|js|ts|sh)$/;
const SHELL_C_RE = /^(?:bash|sh|zsh)\s+-c\s+(["'])(.*)\1$/;

/**
 * A short human name for a check: the last script it invokes before its first
 * flag (so `bash server_python.sh py_organization_check.py --repo …` reads as
 * `py_organization_check.py`), else its leading words.
 */
export function gateCommandLabel(command: string): string {
  // `bash -c "cd server && uv run ruff check ."` is named for what it finally runs.
  const shell = SHELL_C_RE.exec(command.trim());
  if (shell) {
    const last = shell[2].split(/&&|;|\|\|/).pop() ?? "";
    if (last.trim()) return gateCommandLabel(last);
  }
  const words = command.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "Empty check";
  const firstFlag = words.findIndex((w) => w.startsWith("-"));
  const beforeFlags = firstFlag === -1 ? words : words.slice(0, firstFlag);
  const script = [...beforeFlags].reverse().find((w) => SCRIPT_RE.test(w));
  if (script) return script.split("/").pop() ?? script;
  const meaningful = beforeFlags.filter((w) => !LAUNCHERS.has(w));
  return (meaningful.length > 0 ? meaningful : words).slice(0, 3).join(" ");
}

/** Insert `text` into `value` over the selection [start, end); returns the new value and caret. */
export function insertAt(
  value: string,
  text: string,
  start: number,
  end: number,
): { value: string; caret: number } {
  return { value: value.slice(0, start) + text + value.slice(end), caret: start + text.length };
}

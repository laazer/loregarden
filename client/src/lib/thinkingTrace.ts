/**
 * A turn's reasoning transcript, read back into steps and summarised as the one
 * line the operator watches while Baxter works.
 *
 * The server interleaves reasoning prose and tool calls into one string
 * (`services/chat_thinking.py`), writing each tool call as `· name · target` on
 * its own line. That is the only structure this has to recover.
 */

/** A tool step, or a run of reasoning prose. */
export type ThinkingBlock = { kind: "step"; text: string } | { kind: "prose"; text: string };

/** Split the transcript back into what the server interleaved into it. */
export function parseThinkingBlocks(content: string): ThinkingBlock[] {
  const blocks: ThinkingBlock[] = [];
  let prose: string[] = [];

  const flushProse = () => {
    const text = prose.join("\n").trim();
    if (text) blocks.push({ kind: "prose", text });
    prose = [];
  };

  for (const line of content.split("\n")) {
    if (line.startsWith("· ")) {
      flushProse();
      blocks.push({ kind: "step", text: line.slice(2).trim() });
      continue;
    }
    prose.push(line);
  }
  flushProse();
  return blocks;
}

interface Step {
  tool: string;
  target: string;
}

/** What a step is doing, in the operator's words. */
interface Action {
  verb: string;
  /** Empty when the verb says it all. */
  target: string;
}

/** `Read · src/a.py` → tool and target; a bare `Read` has no target yet. */
function splitStep(label: string): Step {
  const at = label.indexOf(" · ");
  if (at < 0) return { tool: label.trim(), target: "" };
  return { tool: label.slice(0, at).trim(), target: label.slice(at + 3).trim() };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A file's name rather than its path; a ticket uuid cut to its first eight. */
function shortName(target: string): string {
  if (UUID.test(target)) return target.slice(0, 8);
  const path = target.replace(/\/+$/, "");
  return path.slice(path.lastIndexOf("/") + 1) || target;
}

function quoted(text: string): string {
  // A pattern holding `\|` is cut at the pipe by `mainCommand`'s split,
  // which can leave a dangling escape.
  const clean = text.replace(/^["']|["']$/g, "").replace(/\\+$/, "");
  return `“${clean.length > 32 ? `${clean.slice(0, 31)}…` : clean}”`;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    // silent-ok: not a parseable URL after all; the raw text is the
    // fallback, and the trace still shows the call in full.
    return url;
  }
}

/** `mcp__loregarden__loregarden_get_ticket` → `get ticket`. */
function mcpToolName(tool: string): string {
  const name = tool.slice(tool.lastIndexOf("__") + 2);
  return name.replace(/^loregarden_/, "").replace(/_/g, " ");
}

const READERS = new Set(["sed", "cat", "head", "tail", "less", "wc", "nl", "bat"]);
const SEARCHERS = new Set(["grep", "rg", "ag", "egrep"]);
const LISTERS = new Set(["ls", "find", "tree", "fd"]);
const SHELL_KEYWORDS = new Set(["for", "while", "if", "until", "case"]);

/** The words of the command that does the work, past `cd`, `timeout` and env. */
function mainCommand(command: string): string[] {
  for (const segment of command.split(/&&|\|\||;|\|/)) {
    const words = segment.trim().split(/\s+/).filter(Boolean);
    while (words.length && /^\w+=/.test(words[0])) words.shift();
    if (words[0] === "timeout") words.splice(0, 2);
    if (words[0] === "env" || words[0] === "time" || words[0] === "sudo") {
      words.shift();
      // `env -u GIT_DIR …`: the flag and its value, then any assignments.
      while (words.length && words[0].startsWith("-")) {
        if (words.shift() === "-u") words.shift();
      }
      while (words.length && /^\w+=/.test(words[0])) words.shift();
    }
    if (!words.length || words[0] === "cd" || words[0] === "pwd" || words[0] === "echo") continue;
    return words;
  }
  return [];
}

/**
 * A shell command as what it does. Most of Baxter's shell steps are reads and
 * searches (`cd … && sed -n 568,612p runner.py`), and "Running sed" says
 * nothing a person would.
 */
function shellAction(command: string): Action {
  const words = mainCommand(command);
  const program = words[0] ? shortName(words[0]) : "";
  const args = words.slice(1).filter((word) => !word.startsWith("-") && !word.startsWith("2>"));
  if (READERS.has(program)) {
    const file = [...args].reverse().find((word) => !/["'()$]/.test(word) && /\/|\.\w+$/.test(word));
    return { verb: "Reading", target: file ? shortName(file) : "" };
  }
  if (SEARCHERS.has(program)) return { verb: "Searching for", target: args[0] ? quoted(args[0]) : "" };
  if (LISTERS.has(program)) return { verb: "Looking in", target: args[0] ? shortName(args[0]) : "" };
  if (program === "git" || program === "gh") {
    // `git -C <dir> log`: the subcommand is the first word that is neither a
    // flag nor the value of `-C`/`-c`.
    const rest = words.slice(1);
    const sub = rest.find((word, index) => !word.startsWith("-") && !["-C", "-c"].includes(rest[index - 1]));
    return { verb: "Running", target: `${program} ${sub ?? ""}`.trim() };
  }
  if (program === "sqlite3") return { verb: "Querying the database", target: "" };
  if (program === "curl") {
    const url = args.find((word) => /^["']?https?:\/\//.test(word));
    return { verb: "Fetching", target: url ? hostOf(url.replace(/^["']|["']$/g, "")) : "" };
  }
  if (!program || SHELL_KEYWORDS.has(program) || program.includes("$(")) return { verb: "Running a script", target: "" };
  return { verb: "Running", target: program };
}

/** A step as what it does. Consecutive steps that read alike merge into one. */
function actionFor({ tool, target }: Step): Action {
  if (tool.startsWith("mcp__")) return { verb: `Calling ${mcpToolName(tool)}`, target: target ? shortName(target) : "" };
  switch (tool.toLowerCase()) {
    case "read":
    case "read_file":
      return { verb: "Reading", target: target ? shortName(target) : "" };
    case "grep":
    case "search":
    case "codebase_search":
      return { verb: "Searching for", target: target ? quoted(target) : "" };
    case "glob":
    case "list_dir":
      return { verb: "Finding", target: target ? shortName(target) : "" };
    case "bash":
    case "shell":
    case "run_terminal_cmd":
      return target ? shellAction(target) : { verb: "Running", target: "" };
    case "edit":
    case "multiedit":
    case "str_replace":
    case "edit_file":
      return { verb: "Editing", target: target ? shortName(target) : "" };
    case "write":
      return { verb: "Writing", target: target ? shortName(target) : "" };
    case "webfetch":
      return { verb: "Fetching", target: target ? hostOf(target) : "" };
    case "websearch":
      return { verb: "Searching the web for", target: target ? quoted(target) : "" };
    case "task":
    case "agent":
      return { verb: "Handing off to a helper", target: "" };
    case "toolsearch":
      return { verb: "Loading tools", target: "" };
    case "todowrite":
      return { verb: "Updating the plan", target: "" };
    case "askuserquestion":
      return { verb: "Asking you a question", target: "" };
    case "monitor":
      return { verb: "Watching", target: target ? shortName(target) : "" };
    default:
      return { verb: tool, target: target ? shortName(target) : "" };
  }
}

/** `a and b`, or `a, b and 3 more` for the two most recent of a longer run. */
function listTargets(targets: string[]): string {
  const distinct = targets.filter((target, index) => target && targets.lastIndexOf(target) === index);
  if (distinct.length === 0) return "";
  if (distinct.length === 1) return distinct[0];
  const [a, b] = distinct.slice(-2);
  if (distinct.length === 2) return `${a} and ${b}`;
  return `${a}, ${b} and ${distinct.length - 2} more`;
}

const PROSE_ACTIVITIES = new Set(["Thinking", "Writing the reply"]);

/**
 * The one line shown while a turn runs: what Baxter is doing now, in words,
 * and how many steps he has taken.
 *
 * Consecutive steps that read alike are one thing to the operator ("Reading
 * runner.py and cli.py", whether through Read or `sed -n`), so the line names
 * the trailing run of them rather than the latest call alone. When he is
 * thinking or writing, it says that; the steps behind it are in the trace.
 */
export function summarizeThinking(content: string, activity: string): string {
  const steps = parseThinkingBlocks(content)
    .filter((block) => block.kind === "step")
    .map((block) => splitStep(block.text));
  const count = steps.length;
  const tally = count === 0 ? "" : ` · ${count} ${count === 1 ? "step" : "steps"}`;

  const now = activity.trim();
  if (PROSE_ACTIVITIES.has(now)) return `${now}${tally}`;

  // The activity can run ahead of the transcript: a tool that has started but
  // whose call is not written yet (its arguments still streaming) is a bare
  // name, and a poll can land between the two writes.
  const actions = steps.map(actionFor);
  const ahead = now ? actionFor(splitStep(now)) : null;
  const last = actions.at(-1);
  if (ahead && !(last && last.verb === ahead.verb && (!ahead.target || last.target === ahead.target))) {
    actions.push(ahead);
  }
  const current = actions.at(-1);
  if (!current) return `Thinking${tally}`;

  const run: string[] = [];
  for (let index = actions.length - 1; index >= 0 && actions[index].verb === current.verb; index -= 1) {
    run.unshift(actions[index].target);
  }
  const targets = listTargets(run);
  // A call that has started but says nothing yet trails off, so the line does
  // not read as finished.
  const pending = current === ahead && !ahead.target;
  const lead = targets ? `${current.verb} ${targets}` : pending ? `${current.verb}…` : current.verb;
  return `${lead}${tally}`;
}

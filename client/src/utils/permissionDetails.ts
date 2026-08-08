export interface PermissionField {
  label: string;
  value: string;
  multiline?: boolean;
  mono?: boolean;
  /** Render the value as markdown rather than preformatted text. */
  markdown?: boolean;
}

export interface PermissionDetailsView {
  toolLabel: string;
  subtitle?: string;
  primary?: PermissionField;
  fields: PermissionField[];
}

const CONTENT_PREVIEW_LIMIT = 2400;
// A plan is the whole thing being approved, so truncating it at the generic
// preview limit would hide the part the decision rests on.
const PLAN_PREVIEW_LIMIT = 20000;

function parseToolInput(raw: string | undefined | null): Record<string, unknown> {
  const text = raw?.trim();
  if (!text || text === "{}") return {};
  try {
    const parsed = JSON.parse(text) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function asString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function truncate(value: string, limit = CONTENT_PREVIEW_LIMIT): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n… (${value.length - limit} more characters)`;
}

function bareMcpToolName(toolName: string): string | null {
  const prefix = "mcp__loregarden__";
  return toolName.startsWith(prefix) ? toolName.slice(prefix.length) : null;
}

function displayToolName(toolName: string): string {
  const bare = bareMcpToolName(toolName);
  if (bare) return bare;
  if (toolName.startsWith("mcp__")) {
    const parts = toolName.split("__");
    return parts[parts.length - 1] || toolName;
  }
  return toolName;
}

function field(
  label: string,
  value: unknown,
  opts?: { multiline?: boolean; mono?: boolean; markdown?: boolean; limit?: number },
): PermissionField | null {
  const text = asString(value).trim();
  if (!text) return null;
  return {
    label,
    value: truncate(text, opts?.limit),
    multiline: opts?.multiline,
    mono: opts?.mono ?? true,
    markdown: opts?.markdown,
  };
}

export function buildPermissionDetails(
  toolName: string,
  toolInputJson: string | undefined | null,
): PermissionDetailsView {
  const input = parseToolInput(toolInputJson);
  const toolLabel = displayToolName(toolName || "tool");
  const fields: PermissionField[] = [];

  const normalized = toolName.toLowerCase();
  // Plan approvals carry the agent's plan as markdown. Rendering it in the
  // generic <pre> path turned headings, lists and links into raw syntax, which
  // is the one field an operator actually has to read before approving.
  if (normalized.replace(/_/g, "").endsWith("exitplanmode")) {
    return {
      toolLabel: "Implementation plan",
      subtitle: toolLabel,
      primary:
        field("Plan", input.plan, { multiline: true, mono: false, markdown: true, limit: PLAN_PREVIEW_LIMIT }) ??
        undefined,
      fields,
    };
  }

  if (normalized === "bash" || normalized.endsWith("__bash")) {
    const command = field("Command", input.command, { multiline: true });
    const description = field("Description", input.description, { multiline: true, mono: false });
    if (description) fields.push(description);
    return {
      toolLabel: "Shell command",
      subtitle: toolLabel,
      primary: command ?? undefined,
      fields,
    };
  }

  if (normalized === "write" || normalized.endsWith("__write")) {
    const path = field("File", input.file_path ?? input.path);
    const content = field("Content", input.content, { multiline: true });
    if (content) fields.push(content);
    return {
      toolLabel: "Write file",
      subtitle: toolLabel,
      primary: path ?? undefined,
      fields,
    };
  }

  if (normalized === "edit" || normalized.endsWith("__edit")) {
    const path = field("File", input.file_path ?? input.path);
    const oldString = field("Replace", input.old_string, { multiline: true });
    const newString = field("With", input.new_string, { multiline: true });
    if (oldString) fields.push(oldString);
    if (newString) fields.push(newString);
    return {
      toolLabel: "Edit file",
      subtitle: toolLabel,
      primary: path ?? undefined,
      fields,
    };
  }

  if (normalized === "read" || normalized.endsWith("__read")) {
    return {
      toolLabel: "Read file",
      subtitle: toolLabel,
      primary: field("File", input.file_path ?? input.path) ?? undefined,
      fields,
    };
  }

  if (normalized === "glob" || normalized.endsWith("__glob")) {
    const pattern = field("Pattern", input.pattern ?? input.glob_pattern);
    const path = field("Path", input.path ?? input.target_directory);
    if (path) fields.push(path);
    return {
      toolLabel: "Find files",
      subtitle: toolLabel,
      primary: pattern ?? undefined,
      fields,
    };
  }

  if (normalized === "grep" || normalized.endsWith("__grep")) {
    const pattern = field("Pattern", input.pattern);
    const path = field("Path", input.path ?? input.include);
    if (path) fields.push(path);
    return {
      toolLabel: "Search contents",
      subtitle: toolLabel,
      primary: pattern ?? undefined,
      fields,
    };
  }

  for (const [key, value] of Object.entries(input)) {
    // Adapters that name the plan tool differently still put the markdown under
    // a `plan` key; treat it the same way rather than falling back to <pre>.
    const isPlan = key === "plan" && typeof value === "string";
    const next = field(key.replace(/_/g, " "), value, {
      multiline: asString(value).includes("\n") || asString(value).length > 120,
      mono: !isPlan,
      markdown: isPlan,
      limit: isPlan ? PLAN_PREVIEW_LIMIT : undefined,
    });
    if (next) fields.push(next);
  }

  return {
    toolLabel: bareMcpToolName(toolName) ? "MCP tool" : "Agent tool",
    subtitle: toolLabel,
    fields,
  };
}

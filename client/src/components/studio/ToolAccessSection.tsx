import type { StudioAgentToolGrants, ToolGrantWarning, ToolPosture } from "../../api/client";

/**
 * Which tools an agent may reach, as opposed to which of them run unattended.
 *
 * Warnings are advisory and never block a save: narrowing on purpose is a
 * legitimate thing to do. They exist because a tool left out of the allowlist
 * fails inside the agent's turn with no approval request and no activity row —
 * this panel is the only place that failure is visible before it happens.
 */

/** Built-in CLI tools an operator can narrow to. Mirrors the server `CliTool` enum. */
const CLI_TOOLS = [
  "Read",
  "Write",
  "Edit",
  "Bash",
  "Glob",
  "Grep",
  "WebFetch",
  "WebSearch",
  "Task",
  "TodoWrite",
  "AskUserQuestion",
  "NotebookEdit",
] as const;

const POSTURES: { value: ToolPosture; label: string; hint: string }[] = [
  {
    value: "inherit",
    label: "Inherit",
    hint: "Whatever the runtime offers. The default, and how every agent behaved before grants existed.",
  },
  {
    value: "allowlist",
    label: "Allowlist",
    hint: "Narrow to the tools chosen below. You can only subtract from what the rail already offers.",
  },
  {
    value: "unrestricted",
    label: "Unrestricted",
    hint: "Explicitly place no limit — reads differently from the default when someone reviews this later.",
  },
];

export function ToolAccessSection({
  grants,
  warnings,
  servers,
  onChange,
}: {
  grants: StudioAgentToolGrants;
  warnings: ToolGrantWarning[];
  servers: string[];
  onChange: (grants: StudioAgentToolGrants) => void;
}) {
  const isAllowlist = grants.posture === "allowlist";

  const toggle = (key: "allowed_tools" | "disallowed_tools" | "mcp_servers", value: string) => {
    const current = grants[key];
    onChange({
      ...grants,
      [key]: current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    });
  };

  return (
    <div className="studio-card">
      <div className="studio-card-header tight">
        <span className="studio-card-title">Tool access</span>
        {warnings.length > 0 && (
          <span className="studio-tool-count-badge">{warnings.length} caution</span>
        )}
      </div>
      <p className="studio-card-hint">
        What this agent may reach at all. Separate from approvals, which decide what runs
        without asking. Only the Claude provider can enforce this.
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: 7, marginBottom: 12 }}>
        {POSTURES.map((posture) => (
          <label key={posture.value} className="studio-mcp-tool-row">
            <input
              type="radio"
              name="tool-posture"
              checked={grants.posture === posture.value}
              onChange={() => onChange({ ...grants, posture: posture.value })}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="studio-mcp-tool-name">{posture.label}</div>
              <div className="studio-mcp-tool-desc">{posture.hint}</div>
            </div>
          </label>
        ))}
      </div>

      {warnings.length > 0 && (
        <ul
          aria-label="Tool access warnings"
          style={{ listStyle: "none", padding: 0, margin: "0 0 12px", display: "grid", gap: 6 }}
        >
          {warnings.map((warning) => (
            <li key={warning.code} className="studio-card-hint" style={{ color: "var(--warn, #d08b28)" }}>
              {warning.message}
              {warning.tools.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                  {warning.tools.map((tool) => (
                    <span key={tool} className="studio-preview-chip">
                      {tool}
                    </span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {isAllowlist && (
        <>
          <div className="studio-card-hint" style={{ marginBottom: 6 }}>
            Allowed CLI tools — leave every box clear to keep the rail's full set.
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {CLI_TOOLS.map((tool) => (
              <label key={tool} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <input
                  type="checkbox"
                  checked={grants.allowed_tools.includes(tool)}
                  onChange={() => toggle("allowed_tools", tool)}
                />
                <span style={{ fontSize: 12.5, color: "var(--txm)" }}>{tool}</span>
              </label>
            ))}
          </div>
        </>
      )}

      {grants.posture !== "inherit" && (
        <>
          <div className="studio-card-hint" style={{ marginBottom: 6 }}>
            MCP servers — none selected grants every enabled server.
          </div>
          {servers.length === 0 ? (
            <div className="studio-card-hint">No MCP servers are registered and enabled.</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {servers.map((server) => (
                <label key={server} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <input
                    type="checkbox"
                    checked={grants.mcp_servers.includes(server)}
                    onChange={() => toggle("mcp_servers", server)}
                  />
                  <span style={{ fontSize: 12.5, color: "var(--txm)" }}>{server}</span>
                </label>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

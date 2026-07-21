import { useEffect, useState } from "react";

import type { McpServerInput, McpServerView } from "../../api/client";

const TRANSPORTS = ["http", "stdio"] as const;

function blank(): McpServerInput {
  return {
    name: "",
    description: "",
    transport: "http",
    url: "",
    command: "",
    args: [],
    auth_env_var: "",
    enabled: true,
    tool_policy: "prompt",
  };
}

/**
 * Register or edit one MCP server.
 *
 * The credential field takes the *name* of an environment variable, never a
 * token — the server stores only the name, so a value typed here would be a
 * secret the operator believes was saved and was not.
 */
export function McpServerForm({
  server,
  isSaving,
  error,
  onSubmit,
  onCancel,
}: {
  server: McpServerView | null;
  isSaving?: boolean;
  error?: string | null;
  onSubmit: (body: McpServerInput) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<McpServerInput>(blank());

  useEffect(() => {
    setDraft(
      server
        ? {
            name: server.name,
            description: server.description,
            transport: server.transport,
            url: server.url,
            command: server.command,
            args: server.args,
            auth_env_var: server.auth_env_var,
            enabled: server.enabled,
            tool_policy: server.tool_policy,
          }
        : blank(),
    );
    // Depends on the whole object: the registry is not polled, so a new
    // reference means a different server was selected or a save came back —
    // both cases where the draft should show what the server now says.
  }, [server]);

  const set = (patch: Partial<McpServerInput>) => setDraft((d) => ({ ...d, ...patch }));
  const canSave = draft.name.trim().length > 0 && !isSaving;

  return (
    <form
      className="mcp-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) onSubmit(draft);
      }}
    >
      <div className="modal-field">
        <label className="modal-field-label" htmlFor="mcp-name">
          Name
        </label>
        <input
          id="mcp-name"
          className="btn-secondary filter-select"
          value={draft.name}
          onChange={(e) => set({ name: e.target.value })}
          placeholder="github"
        />
      </div>

      <div className="modal-field">
        <label className="modal-field-label" htmlFor="mcp-transport">
          Transport
        </label>
        <select
          id="mcp-transport"
          className="btn-secondary filter-select"
          value={draft.transport}
          onChange={(e) => set({ transport: e.target.value })}
        >
          {TRANSPORTS.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      {draft.transport === "http" ? (
        <div className="modal-field">
          <label className="modal-field-label" htmlFor="mcp-url">
            URL
          </label>
          <input
            id="mcp-url"
            className="btn-secondary filter-select"
            value={draft.url ?? ""}
            onChange={(e) => set({ url: e.target.value })}
            placeholder="https://mcp.example.com/sse"
          />
        </div>
      ) : (
        <div className="modal-field">
          <label className="modal-field-label" htmlFor="mcp-command">
            Command
          </label>
          <input
            id="mcp-command"
            className="btn-secondary filter-select"
            value={draft.command ?? ""}
            onChange={(e) => set({ command: e.target.value })}
            placeholder="npx -y @scope/mcp-server"
          />
        </div>
      )}

      <div className="modal-field">
        <label className="modal-field-label" htmlFor="mcp-auth">
          Credential env var
        </label>
        <input
          id="mcp-auth"
          className="btn-secondary filter-select"
          value={draft.auth_env_var ?? ""}
          onChange={(e) => set({ auth_env_var: e.target.value })}
          placeholder="GITHUB_MCP_TOKEN"
        />
        <p className="modal-subtitle mcp-form-hint">
          The variable&rsquo;s name, not its value. Loregarden reads it from the environment
          when it starts an agent, and never stores the token.
        </p>
      </div>

      <div className="modal-field">
        <label className="modal-field-label" htmlFor="mcp-policy">
          When an agent calls this server
        </label>
        <select
          id="mcp-policy"
          className="btn-secondary filter-select"
          value={draft.tool_policy ?? "prompt"}
          onChange={(e) => set({ tool_policy: e.target.value })}
        >
          <option value="prompt">Ask me every time</option>
          <option value="auto">Run without asking</option>
        </select>
        <p className="modal-subtitle mcp-form-hint">
          Trust applies to the whole server. An unattended run stops on every call
          while this is set to ask.
        </p>
      </div>

      <label className="mcp-form-toggle">
        <input
          type="checkbox"
          checked={draft.enabled ?? true}
          onChange={(e) => set({ enabled: e.target.checked })}
        />
        Available to agents
      </label>

      {error && <div className="mcp-form-error">{error}</div>}

      <div className="modal-footer">
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={isSaving}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={!canSave}>
          {isSaving ? "Saving…" : server ? "Save changes" : "Register server"}
        </button>
      </div>
    </form>
  );
}

import type { StudioMcpToolGuide } from "../../api/client";

export function McpToolGuideSection({
  guides,
  enabled,
  selected,
  onToggleEnabled,
  onToggleTool,
  variant = "default",
}: {
  guides: StudioMcpToolGuide[];
  enabled: boolean;
  selected: string[];
  onToggleEnabled: (enabled: boolean) => void;
  onToggleTool: (tool: string) => void;
  variant?: "default" | "studio";
}) {
  const stageGuides = guides.filter((g) => g.stage_agent);
  const orchestratorGuides = guides.filter((g) => g.orchestrator_only);
  const isStudio = variant === "studio";

  if (isStudio) {
    return (
      <div className="studio-card">
        <div className="studio-card-header tight">
          <span className="studio-card-icon amber" aria-hidden>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14.7 6.3a4 4 0 0 1-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 1 5.4-5.4l-2.6 2.6-1.4-1.4 2.6-2.6z" />
            </svg>
          </span>
          <span className="studio-card-title">Loregarden MCP tools</span>
          {enabled && (
            <span className="studio-tool-count-badge">{selected.length} on</span>
          )}
        </div>
        <p className="studio-card-hint">
          Enable the tools this role may call directly during a stage.
        </p>
        <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onToggleEnabled(e.target.checked)}
            style={{ accentColor: "var(--ac)" }}
          />
          <span style={{ fontSize: 12.5, color: "var(--txm)" }}>Enable MCP tool access</span>
        </label>
        {enabled && (
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {stageGuides.map((guide) => (
              <label key={guide.name} className="studio-mcp-tool-row">
                <input
                  type="checkbox"
                  checked={selected.includes(guide.name)}
                  onChange={() => onToggleTool(guide.name)}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="studio-mcp-tool-name">{guide.name}</div>
                  <div className="studio-mcp-tool-desc">{guide.description}</div>
                </div>
              </label>
            ))}
            {orchestratorGuides.length > 0 && (
              <>
                <p className="studio-card-hint" style={{ marginTop: 8, marginBottom: 0 }}>
                  Orchestrator-only tools are usually not enabled for stage agents.
                </p>
                {orchestratorGuides.map((guide) => (
                  <label key={guide.name} className="studio-mcp-tool-row" style={{ opacity: 0.85 }}>
                    <input
                      type="checkbox"
                      checked={selected.includes(guide.name)}
                      onChange={() => onToggleTool(guide.name)}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="studio-mcp-tool-name">{guide.name}</div>
                      <div className="studio-mcp-tool-desc">{guide.description}</div>
                    </div>
                  </label>
                ))}
              </>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <section style={{ marginTop: 16 }}>
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => onToggleEnabled(e.target.checked)} />
        <span className="modal-section-title" style={{ margin: 0 }}>
          Loregarden MCP tools
        </span>
      </label>

      {enabled && (
        <>
          <p className="modal-hint" style={{ marginTop: 0 }}>
            Stage agents — enable tools this role should call directly.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
            {stageGuides.map((guide) => (
              <McpToolCard
                key={guide.name}
                guide={guide}
                checked={selected.includes(guide.name)}
                onToggle={() => onToggleTool(guide.name)}
              />
            ))}
          </div>

          {orchestratorGuides.length > 0 && (
            <>
              <p className="modal-hint">Orchestrator-only — reference for workflow drivers, usually not stage agents.</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {orchestratorGuides.map((guide) => (
                  <McpToolCard
                    key={guide.name}
                    guide={guide}
                    checked={selected.includes(guide.name)}
                    onToggle={() => onToggleTool(guide.name)}
                    dimmed
                  />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

function McpToolCard({
  guide,
  checked,
  onToggle,
  dimmed,
}: {
  guide: StudioMcpToolGuide;
  checked: boolean;
  onToggle: () => void;
  dimmed?: boolean;
}) {
  return (
    <details
      className="state-card"
      open={checked}
      style={{ padding: "8px 12px", opacity: dimmed ? 0.85 : 1 }}
    >
      <summary style={{ cursor: "pointer", listStyle: "none", display: "flex", alignItems: "flex-start", gap: 8 }}>
        <input type="checkbox" checked={checked} onChange={onToggle} onClick={(e) => e.stopPropagation()} />
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, fontWeight: 600 }}>{guide.name}</div>
          <div style={{ fontSize: 11.5, color: "var(--txl)", marginTop: 2 }}>{guide.description}</div>
        </div>
      </summary>
      <div style={{ marginTop: 10, paddingLeft: 24, fontSize: 11.5, lineHeight: 1.5 }}>
        <div style={{ marginBottom: 6 }}>
          <span className="state-label">When to use</span>
          <div>{guide.when_to_use}</div>
        </div>
        <div>
          <span className="state-label">Example</span>
          <pre
            style={{
              margin: "4px 0 0",
              padding: 8,
              background: "var(--bg0)",
              borderRadius: 4,
              fontSize: 10.5,
              overflow: "auto",
              whiteSpace: "pre-wrap",
            }}
          >
            {guide.example}
          </pre>
        </div>
      </div>
    </details>
  );
}

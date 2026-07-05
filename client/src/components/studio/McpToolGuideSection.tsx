import type { StudioMcpToolGuide } from "../../api/client";

export function McpToolGuideSection({
  guides,
  enabled,
  selected,
  onToggleEnabled,
  onToggleTool,
}: {
  guides: StudioMcpToolGuide[];
  enabled: boolean;
  selected: string[];
  onToggleEnabled: (enabled: boolean) => void;
  onToggleTool: (tool: string) => void;
}) {
  const stageGuides = guides.filter((g) => g.stage_agent);
  const orchestratorGuides = guides.filter((g) => g.orchestrator_only);

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

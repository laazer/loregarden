import { useId, useState } from "react";

import type { StudioMcpToolGuide } from "../../api/client";
import { Input } from "../ui/Input";

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
      <StudioMcpTools
        stageGuides={stageGuides}
        orchestratorGuides={orchestratorGuides}
        enabled={enabled}
        selected={selected}
        onToggleEnabled={onToggleEnabled}
        onToggleTool={onToggleTool}
      />
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
        <Input type="checkbox" aria-label={guide.name} checked={checked} onChange={onToggle} onClick={(e) => e.stopPropagation()} />
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: "var(--fs-micro)", fontWeight: 600 }}>{guide.name}</div>
          <div style={{ fontSize: "var(--fs-micro)", color: "var(--txl)", marginTop: 2 }}>{guide.description}</div>
        </div>
      </summary>
      <div style={{ marginTop: 10, paddingLeft: 24, fontSize: "var(--fs-micro)", lineHeight: 1.5 }}>
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
              fontSize: "var(--fs-micro)",
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

/**
 * The agent page's tool picker. A count and the on/off switch say what the
 * agent has; the list itself (seventy-odd tools) is folded and filterable,
 * two columns of one-line rows, instead of a card per tool down the page.
 */
function StudioMcpTools({
  stageGuides,
  orchestratorGuides,
  enabled,
  selected,
  onToggleEnabled,
  onToggleTool,
}: {
  stageGuides: StudioMcpToolGuide[];
  orchestratorGuides: StudioMcpToolGuide[];
  enabled: boolean;
  selected: string[];
  onToggleEnabled: (enabled: boolean) => void;
  onToggleTool: (tool: string) => void;
}) {
  const baseId = useId();
  const [filter, setFilter] = useState("");
  const needle = filter.trim().toLowerCase();
  const matches = (guide: StudioMcpToolGuide) =>
    !needle || guide.name.toLowerCase().includes(needle) || guide.description.toLowerCase().includes(needle);
  const total = stageGuides.length + orchestratorGuides.length;

  const rows = (guides: StudioMcpToolGuide[]) =>
    guides.filter(matches).map((guide) => (
      <label key={guide.name} className="studio-mcp-tool-row" title={guide.description}>
        <Input
          type="checkbox"
          checked={selected.includes(guide.name)}
          onChange={() => onToggleTool(guide.name)}
          aria-describedby={`${baseId}-${guide.name}`}
        />
        <span className="studio-mcp-tool-text">
          <span className="studio-mcp-tool-name">{guide.name}</span>
          <span id={`${baseId}-${guide.name}`} className="studio-mcp-tool-desc">
            {guide.description}
          </span>
        </span>
      </label>
    ));

  return (
    <section className="studio-record-section" aria-labelledby={`${baseId}-title`}>
      <h3 id={`${baseId}-title`} className="studio-record-section-title">
        MCP tools
      </h3>
      <label className="studio-mcp-switch">
        <Input type="checkbox" checked={enabled} onChange={(e) => onToggleEnabled(e.target.checked)} />
        <span>
          Loregarden MCP tool access
          {enabled ? ` · ${selected.length} of ${total} on` : " · off"}
        </span>
      </label>
      {enabled ? (
        <details className="studio-mcp-picker">
          <summary>Choose tools</summary>
          <Input
            type="search"
            className="studio-mcp-filter"
            aria-label="Filter MCP tools"
            placeholder="Filter tools"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="studio-mcp-grid">{rows(stageGuides)}</div>
          {orchestratorGuides.some(matches) ? (
            <>
              <p className="studio-record-section-hint">
                Orchestrator-only: usually not enabled for stage agents.
              </p>
              <div className="studio-mcp-grid is-dim">{rows(orchestratorGuides)}</div>
            </>
          ) : null}
          {needle && !stageGuides.some(matches) && !orchestratorGuides.some(matches) ? (
            <p className="studio-record-muted">No tool matches “{filter}”.</p>
          ) : null}
        </details>
      ) : null}
    </section>
  );
}

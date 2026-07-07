import type { StudioAgentPreview } from "../../api/client";
import { MarkdownContent } from "../chat/MarkdownContent";

export function AgentPreviewPanel({
  preview,
  loading,
  slug,
}: {
  preview: StudioAgentPreview | undefined;
  loading: boolean;
  slug?: string;
}) {
  const fileLabel = slug ? `${slug}.system.md` : "agent.system.md";

  return (
    <aside className="studio-preview studio-preview--agent">
      <div className="studio-preview-live">
        <span className="studio-preview-live-dot" aria-hidden />
        <span className="studio-preview-live-label">Live assembled prompt</span>
      </div>
      <p className="studio-preview-hint">role + MCP + gates + hand-offs</p>
      {preview?.sections && preview.sections.length > 0 && (
        <div className="studio-preview-chips">
          {preview.sections.map((section) => (
            <span key={section} className="studio-preview-chip">
              {section}
            </span>
          ))}
        </div>
      )}
      <div className="studio-preview-terminal">
        <div className="studio-preview-terminal-bar">
          <span className="studio-preview-terminal-dot red" aria-hidden />
          <span className="studio-preview-terminal-dot amber" aria-hidden />
          <span className="studio-preview-terminal-dot green" aria-hidden />
          <span className="studio-preview-terminal-title">{fileLabel}</span>
        </div>
        <div className="studio-preview-terminal-body">
          {loading && <p className="studio-preview-hint">Updating preview…</p>}
          {!loading && preview?.markdown && (
            <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
              <MarkdownContent content={preview.markdown} />
            </div>
          )}
          {!loading && !preview?.markdown && (
            <p className="studio-preview-hint">Select or edit an agent to see the assembled prompt.</p>
          )}
        </div>
      </div>
    </aside>
  );
}

import ReactMarkdown from "react-markdown";

import type { StudioAgentPreview } from "../../api/client";

export function AgentPreviewPanel({
  preview,
  loading,
}: {
  preview: StudioAgentPreview | undefined;
  loading: boolean;
}) {
  return (
    <aside
      style={{
        width: 360,
        borderLeft: "1px solid var(--bd)",
        background: "var(--bg0)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--bd)" }}>
        <div className="modal-section-title" style={{ margin: 0 }}>
          Agent preview
        </div>
        <p className="modal-hint" style={{ margin: "4px 0 0" }}>
          Full assembled prompt (role + MCP + gates + handoffs)
        </p>
        {preview?.sections && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
            {preview.sections.map((section) => (
              <span
                key={section}
                style={{
                  fontSize: 10,
                  padding: "2px 6px",
                  borderRadius: 3,
                  background: "var(--bg1)",
                  color: "var(--txl)",
                  fontFamily: "var(--mono)",
                }}
              >
                {section}
              </span>
            ))}
          </div>
        )}
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 14 }}>
        {loading && <p className="modal-hint">Updating preview…</p>}
        {!loading && preview?.markdown && (
          <div className="markdown-preview">
            <ReactMarkdown>{preview.markdown}</ReactMarkdown>
          </div>
        )}
        {!loading && !preview?.markdown && (
          <p className="modal-hint">Select or edit an agent to see the assembled prompt.</p>
        )}
      </div>
    </aside>
  );
}

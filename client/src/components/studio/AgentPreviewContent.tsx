import type { StudioAgentPreview } from "../../api/client";
import { MarkdownContent } from "../chat/MarkdownContent";

const PREVIEW_SECTIONS = [
  { key: "header", label: "header" },
  { key: "role", label: "role" },
  { key: "mcp_tools", label: "mcp_tools" },
  { key: "gates", label: "gates" },
  { key: "handoffs", label: "hand-offs" },
  { key: "permissions", label: "permissions" },
] as const;

function sectionActive(sectionKey: string, sections: string[] | undefined): boolean {
  if (!sections?.length) return sectionKey === "header";
  if (sectionKey === "mcp_tools") {
    return sections.some((item) =>
      ["mcp_tools", "mcp_module", "memory_protocol_module"].includes(item),
    );
  }
  return sections.includes(sectionKey);
}

function formatProviderLabel(provider: string): string {
  switch (provider) {
    case "claude":
      return "Claude Code";
    case "cursor":
      return "Cursor Agent";
    case "lmstudio":
      return "LM Studio";
    case "local":
      return "Local runner";
    default:
      return provider || "—";
  }
}

function AgentPreviewIdentity({ preview }: { preview: StudioAgentPreview }) {
  const { profile } = preview;
  const metaItems = [
    { label: "Provider", value: formatProviderLabel(profile.provider) },
    { label: "Skill", value: profile.default_skill || "—", mono: true },
    ...(profile.model ? [{ label: "Model", value: profile.model, mono: true }] : []),
    ...(profile.timeout ? [{ label: "Timeout", value: `${profile.timeout}s`, mono: true }] : []),
    ...(profile.always_apply != null
      ? [{ label: "Always apply", value: profile.always_apply ? "Yes" : "No" }]
      : []),
  ];

  return (
    <div className="studio-preview-identity">
      <div className="studio-preview-identity-name">{preview.name || "Untitled agent"}</div>
      {profile.description ? (
        <div className="studio-preview-identity-desc">{profile.description}</div>
      ) : null}
      <div className="studio-preview-identity-meta">
        {metaItems.map((item) => (
          <div key={item.label} className="studio-preview-identity-meta-item">
            <div className="studio-preview-identity-meta-label">{item.label}</div>
            <div className={`studio-preview-identity-meta-value${item.mono ? " mono" : ""}`}>
              {item.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AgentPreviewContent({
  preview,
  loading,
  slug,
  compact = false,
  showMeta = true,
}: {
  preview: StudioAgentPreview | undefined;
  loading: boolean;
  slug?: string;
  compact?: boolean;
  showMeta?: boolean;
}) {
  const fileLabel = slug ? `${slug}.system.md` : "agent.system.md";

  return (
    <>
      {showMeta ? (
        <>
          <p className="studio-preview-hint">role + MCP + gates + hand-offs</p>
          {preview?.sections && preview.sections.length > 0 && (
            <div className="studio-preview-chips">
              {PREVIEW_SECTIONS.map(({ key, label }) => {
                const active = sectionActive(key, preview.sections);
                return (
                  <span
                    key={key}
                    className={`studio-preview-chip${active ? " studio-preview-chip--active" : ""}`}
                  >
                    {label}
                  </span>
                );
              })}
            </div>
          )}
        </>
      ) : null}
      <div className="studio-preview-card">
        <div className="studio-preview-card-bar">
          <span className="studio-preview-card-title">{fileLabel}</span>
        </div>
        <div className={`studio-preview-card-body${compact ? " studio-preview-card-body--compact" : ""}`}>
          {loading && <p className="studio-preview-hint studio-preview-hint--inline">Updating preview…</p>}
          {!loading && preview && (preview.name || preview.profile.description) ? (
            <AgentPreviewIdentity preview={preview} />
          ) : null}
          {!loading && preview?.markdown && (
            <div className="studio-preview-doc">
              <MarkdownContent content={preview.markdown} normalize={false} />
            </div>
          )}
          {!loading && !preview?.markdown && (
            <p className="studio-preview-hint studio-preview-hint--inline">
              Select or edit an agent to see the assembled prompt.
            </p>
          )}
        </div>
      </div>
    </>
  );
}

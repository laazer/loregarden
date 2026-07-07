import { useState } from "react";

import type { StudioAgentPreview } from "../../api/client";
import { AgentPreviewContent } from "./AgentPreviewContent";
import { AgentPreviewModal } from "./AgentPreviewModal";

export function AgentPreviewPanel({
  preview,
  loading,
  slug,
}: {
  preview: StudioAgentPreview | undefined;
  loading: boolean;
  slug?: string;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const canExpand = Boolean(preview?.markdown);

  return (
    <>
      <aside className="studio-preview studio-preview--agent">
        <div className="studio-preview-header">
          <div className="studio-preview-live">
            <span className="studio-preview-live-dot" aria-hidden />
            <span className="studio-preview-live-label">Live assembled prompt</span>
          </div>
          <button
            type="button"
            className="studio-preview-expand-btn"
            disabled={!canExpand}
            onClick={() => setModalOpen(true)}
            title={canExpand ? "Open full preview" : "Add agent details to preview"}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
            </svg>
            Expand
          </button>
        </div>
        <AgentPreviewContent preview={preview} loading={loading} slug={slug} compact />
      </aside>

      <AgentPreviewModal
        open={modalOpen}
        preview={preview}
        loading={loading}
        slug={slug}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
}

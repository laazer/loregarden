import { createPortal } from "react-dom";

import type { StudioAgentPreview } from "../../api/client";
import { IconCloseButton } from "../IconCloseButton";
import { AgentPreviewContent } from "./AgentPreviewContent";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";

export function AgentPreviewModal({
  open,
  preview,
  loading,
  slug,
  onClose,
}: {
  open: boolean;
  preview: StudioAgentPreview | undefined;
  loading: boolean;
  slug?: string;
  onClose: () => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  if (!open) return null;

  const fileLabel = slug ? `${slug}.system.md` : "agent.system.md";

  return createPortal(
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel studio-preview-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-preview-modal-title"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Agent Studio</div>
            <h2 id="agent-preview-modal-title" className="modal-title">
              Assembled prompt
            </h2>
            <p className="modal-subtitle">{fileLabel}</p>
          </div>
          <IconCloseButton onClick={onClose} />
        </div>
        <div className="studio-preview-modal-scroll">
          <AgentPreviewContent preview={preview} loading={loading} slug={slug} showMeta={false} />
        </div>
      </div>
    </>,
    document.body,
  );
}

import { createPortal } from "react-dom";

import type { StudioAgentPreview } from "../../api/client";
import { IconCloseButton } from "../IconCloseButton";
import { AgentPreviewContent } from "./AgentPreviewContent";
import { ModalShell } from "../ui/ModalShell";

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
  const fileLabel = slug ? `${slug}.system.md` : "agent.system.md";

  // Portalled whether open or not: the shell has to stay in the same place in
  // the tree for its exit to play when `open` turns false.
  return createPortal(
    <ModalShell
      open={open}
      onDismiss={onClose}
      labelledBy="agent-preview-modal-title"
      panelClassName="studio-preview-modal"
    >
      {open ? (
        <>
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
        </>
      ) : null}
    </ModalShell>,
    document.body,
  );
}

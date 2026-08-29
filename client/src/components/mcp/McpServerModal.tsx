import { useEffect } from "react";

import type { McpServerInput, McpServerView } from "../../api/client";
import { IconCloseButton } from "../IconCloseButton";
import { McpServerForm } from "./McpServerForm";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";

/**
 * Register or edit a server, over the gateway rather than instead of it.
 *
 * A modal because registering is a short, self-contained act and the comp
 * treats it as one — the registry, the switchboard and the rules stay visible
 * behind it, which is the context an operator is deciding in.
 */
export function McpServerModal({
  open,
  server,
  isSaving,
  error,
  onSubmit,
  onClose,
}: {
  open: boolean;
  /** The server being edited, or null to register a new one. */
  server: McpServerView | null;
  isSaving: boolean;
  error: string | null;
  onSubmit: (body: McpServerInput) => void;
  onClose: () => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="modal-overlay" data-testid="modal-backdrop" onClick={onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-labelledby="mcp-server-modal-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <h2 className="modal-title" id="mcp-server-modal-title">
              {server ? `Edit ${server.name}` : "Register MCP server"}
            </h2>
            <p className="modal-subtitle">
              A registered server is composed into every agent&rsquo;s MCP config at start —
              there is no per-agent grant, so registering it grants it to all of them.
            </p>
          </div>
          <IconCloseButton onClick={onClose} />
        </div>
        <div className="modal-body">
          <McpServerForm
            server={server}
            isSaving={isSaving}
            error={error}
            onSubmit={onSubmit}
            onCancel={onClose}
          />
        </div>
      </div>
    </>
  );
}

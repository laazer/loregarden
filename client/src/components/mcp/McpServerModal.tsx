
import type { McpServerInput, McpServerView } from "../../api/client";
import { IconCloseButton } from "../IconCloseButton";
import { McpServerForm } from "./McpServerForm";
import { ModalShell } from "../ui/ModalShell";

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

  if (!open) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="mcp-server-modal-title">{null}</ModalShell>;
  }

  return (
    <ModalShell open onDismiss={onClose} labelledBy="mcp-server-modal-title">
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
    </ModalShell>
  );
}

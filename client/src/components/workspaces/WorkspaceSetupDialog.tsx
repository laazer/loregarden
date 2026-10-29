/**
 * Everything a workspace's setup card used to open onto, in one dialog: its
 * installers (installed in place, uncommitted), its gate presets and its
 * launch templates. The panels are unchanged; the table row that opens this
 * carries the faster fix, a pull request, beside each need.
 *
 * A setup card stayed mounted when folded, so ticked gate presets survived it;
 * a dialog does not, so closing over unsaved ticks asks first.
 */

import { useState } from "react";

import type { WorkspaceTemplates } from "../../api/localInstancesTypes";
import { WorkspaceIntegrationPanel } from "../instances/WorkspaceIntegrationPanel";
import { WorkspaceTemplatesPanel } from "../instances/WorkspaceTemplatesPanel";
import { IconCloseButton } from "../IconCloseButton";
import { ModalShell } from "../ui/ModalShell";
import { WorkspaceGatePresetsPanel } from "./WorkspaceGatePresetsPanel";

export function WorkspaceSetupDialog({
  workspace,
  onClose,
}: {
  workspace: WorkspaceTemplates | null;
  onClose: () => void;
}) {
  const [dirty, setDirty] = useState(false);
  const close = () => {
    if (dirty && workspace && !window.confirm(`Discard ${workspace.name}'s unsaved gate changes?`)) return;
    setDirty(false);
    onClose();
  };
  return (
    <ModalShell
      open={workspace !== null}
      onDismiss={close}
      labelledBy="workspace-setup-title"
      panelClassName="workspace-setup-dialog"
    >
      {workspace ? (
        <>
          <div className="modal-header">
            <div>
              <div className="state-label">Workspace setup</div>
              <h2 id="workspace-setup-title" className="modal-title">
                {workspace.name}
              </h2>
              <p className="modal-subtitle">{workspace.repo_root}</p>
            </div>
            <IconCloseButton onClick={close} />
          </div>
          <div className="modal-body workspace-setup-body">
            <WorkspaceIntegrationPanel workspace={workspace} />
            <WorkspaceGatePresetsPanel slug={workspace.slug} name={workspace.name} onDirtyChange={setDirty} />
            <WorkspaceTemplatesPanel workspace={workspace} />
          </div>
        </>
      ) : null}
    </ModalShell>
  );
}

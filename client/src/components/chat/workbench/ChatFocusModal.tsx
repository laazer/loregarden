import { useId, useState } from "react";

import type { ChatFocus } from "../../../api/chatWorkbenchTypes";
import { describeError } from "../../../state/toastStore";
import { IconCloseButton } from "../../IconCloseButton";
import { ReferenceTargetsPicker, type TargetPickerLabels } from "../../memory/ReferenceTargetsPicker";
import "../../memory/ReferenceLibrary.css";
import { Button } from "../../ui/Button";
import { ModalShell } from "../../ui/ModalShell";

const FOCUS_LABELS: TargetPickerLabels = {
  workspaces: "Workspaces to answer about",
  initiatives: "Initiatives to work on",
  fixedWorkspace: "this chat's own",
  fixedInitiative: "this chat's own",
};

/**
 * Pick any number of workspaces and initiatives for one chat thread.
 *
 * Ticks replace the thread's focus as a whole on submit. What the thread was
 * opened on shows as fixed, since it stays in scope regardless. Ticking no
 * workspace on a global thread means every workspace, which the hint says.
 */
export function ChatFocusModal({
  open,
  focus,
  isGlobal,
  saving,
  onSave,
  onClose,
}: {
  open: boolean;
  focus: ChatFocus;
  isGlobal: boolean;
  saving: boolean;
  /** Resolves on success; rejects with the reason, which stays in the dialog. */
  onSave: (body: { workspace_slugs: string[]; initiative_ids: string[] }) => Promise<unknown>;
  onClose: () => void;
}) {
  const titleId = useId();
  const fixedWorkspaces = focus.workspaces.filter((w) => w.fixed).map((w) => w.slug);
  const fixedInitiatives = focus.initiatives.filter((i) => i.fixed).map((i) => i.id);
  const [workspaces, setWorkspaces] = useState(() =>
    focus.workspaces.filter((w) => !w.fixed).map((w) => w.slug),
  );
  const [initiatives, setInitiatives] = useState(() =>
    focus.initiatives.filter((i) => !i.fixed).map((i) => i.id),
  );
  const [failure, setFailure] = useState("");

  const total =
    fixedWorkspaces.length + workspaces.length + fixedInitiatives.length + initiatives.length;
  const everyWorkspace = isGlobal && workspaces.length === 0;

  return (
    <ModalShell
      open={open}
      onDismiss={saving ? undefined : onClose}
      labelledBy={titleId}
      panelClassName="modal-panel-wide"
    >
      <form
        aria-label="Chat focus"
        onSubmit={(event) => {
          event.preventDefault();
          if (saving) return;
          setFailure("");
          onSave({ workspace_slugs: workspaces, initiative_ids: initiatives }).then(onClose, (error) =>
            setFailure(describeError(error, "the focus was not saved")),
          );
        }}
      >
        <div className="modal-header">
          <div>
            <h2 id={titleId} className="modal-title">
              What this chat works across
            </h2>
            <p className="modal-subtitle">
              Baxter answers about the ticked workspaces and reads each ticked initiative's plan,
              from the next message on.
              {everyWorkspace ? " With no workspace ticked, it answers across every workspace." : ""}
            </p>
          </div>
          <IconCloseButton disabled={saving} onClick={onClose} />
        </div>
        <div className="modal-body rl-modal-body">
          <ReferenceTargetsPicker
            fixedWorkspaces={fixedWorkspaces}
            workspaces={workspaces}
            onWorkspaces={setWorkspaces}
            fixedInitiatives={fixedInitiatives}
            initiatives={initiatives}
            onInitiatives={setInitiatives}
            labels={FOCUS_LABELS}
          />
          {failure ? (
            <p className="rl-warn" role="alert">
              Focus not changed: {failure}
            </p>
          ) : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "Saving…" : everyWorkspace && total === 0 ? "Use every workspace" : `Focus on ${total}`}
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}

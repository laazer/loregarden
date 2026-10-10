/**
 * The pane settings form, in a dialog rather than under the pane's header.
 *
 * 557 moved the *picker* out of the pane for two reasons, and both of them were
 * already true of this form — it was simply not measured at the time:
 *
 * 1. **It does not fit.** 554's own review measured a 167px form inside a 149px
 *    pane, its Save button below the fold with no scrollbar to say so. A pane
 *    is whatever size the grid or the canvas made it, and a form drawn inside
 *    one is a form the operator may not be able to finish. The primitives with
 *    three fields are worse than the one that was measured.
 * 2. **A zoomed canvas scales it.** 442 applies `transform: scale()` at every
 *    zoom that is not 100%, and a panel in the React tree beneath it is laid
 *    out inside that transform — so the settings form shrank with the pane it
 *    belonged to, at exactly the zoom levels where the pane was too small to
 *    read.
 *
 * `createPortal` to `document.body` answers both. It lives here beside
 * `PrimitivePickerModal` rather than under `components/views/` for the reason
 * that file records: the suites there scan every source and stylesheet they
 * find and reject `position: fixed`, correctly, for something drawn *inside* a
 * pane. This is drawn over the whole app.
 *
 * ## What it does not do
 *
 * It adds no state. The draft, the refusals and the write all still belong to
 * `PaneSettingsEditor`, which did not change: this is a frame around it, and the
 * `onDone` it already had is what closes the dialog.
 */

import { useId } from "react";
import { createPortal } from "react-dom";

import { IconCloseButton } from "./IconCloseButton";
import { PaneSettingsEditor } from "./views/PaneSettingsEditor";
import type { RegisteredPrimitive } from "./views/primitives/types";
import { ModalShell } from "./ui/ModalShell";

export interface PaneSettingsModalProps {
  containerId: string;
  /** The container as the layout stores it: unvalidated, and possibly absent. */
  container: unknown;
  primitive: RegisteredPrimitive;
  onClose: () => void;
}

/**
 * The shell stays mounted so a close can play its exit; the editor mounts only
 * while open (and while there is a primitive to edit), so each visit starts
 * from the stored settings.
 */
export function PaneSettingsModal({
  open,
  primitive,
  ...props
}: Omit<PaneSettingsModalProps, "primitive"> & {
  open: boolean;
  primitive: RegisteredPrimitive | undefined;
}) {
  // A grid of panes can have more than one of these mounted, so the title's id
  // is per instance rather than a constant.
  const titleId = useId();
  const shown = open && primitive !== undefined;
  return createPortal(
    <ModalShell open={shown} onDismiss={props.onClose} labelledBy={titleId}>
      {shown ? <PaneSettingsBody {...props} primitive={primitive} titleId={titleId} /> : null}
    </ModalShell>,
    document.body,
  );
}

function PaneSettingsBody({
  containerId,
  container,
  primitive,
  onClose,
  titleId,
}: PaneSettingsModalProps & { titleId: string }) {
  return (
    <>
      <div className="modal-header">
        <div>
          <div className="state-label">Pane</div>
          <h2 id={titleId} className="modal-title">
            {primitive.displayName} settings
          </h2>
          <p className="modal-subtitle">
            What this pane shows. Changing the contents is a separate control.
          </p>
        </div>
        <IconCloseButton onClick={onClose} />
      </div>

      <div className="modal-body">
        {/* Keyed by the primitive for the reason the header's panel was: a
            pick made while this is open is a different schema, and a form
            that kept its draft across that would hold one primitive's values
            against another's fields. */}
        <PaneSettingsEditor
          key={primitive.id}
          containerId={containerId}
          container={container}
          primitive={primitive}
          onDone={onClose}
        />
      </div>
    </>
  );
}

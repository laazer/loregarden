/**
 * One container's pane: either the prompt an unconfigured container shows, or
 * the primitive it named.
 *
 * It lives beside `PrimitivePicker` rather than inside the `/view/:viewId` page
 * because a grid leaf (440) and a canvas item (442) both put one of these on
 * screen. Page-local, each renderer would re-derive the empty-vs-configured
 * branch and the rule that a pick *replaces* the container — the re-derivation
 * the primitive registry exists to prevent.
 *
 * The pane obtains its own write through `useViewLayoutWrite`, so neither
 * renderer carries a handler through its recursion. The identity that write
 * needs comes from the same two places the page reads it from: the chrome's
 * workspace and the route's view id.
 */

import { useState } from "react";
import { useParams } from "react-router-dom";

import { useViewLayoutWrite } from "../../hooks/useViewLayoutEdit";
import { asJson } from "../../lib/viewLayouts";
import { useSidebarWorkspaceSlug } from "../../state/SidebarWorkspaceContext";
import { paneSettings } from "./paneChrome";
import { PrimitivePickerModal } from "../PrimitivePickerModal";
import "./paneChrome.css";
import { ContainerPrimitiveHost } from "./primitives/registry";
import { containerKindOf } from "./primitives/types";

/**
 * A container with no `primitive_id` yet — the state a freshly seeded grid opens
 * in, and the whole of AC6.
 *
 * The pick *replaces* the container rather than merging an id into it: the
 * placeholder is stored as `kind: "panel"`, and a terminal primitive stored
 * under `panel` is a disagreement `ContainerPrimitiveHost` refuses to mount.
 */
function EmptyContainerPrompt({
  containerId,
  onPick,
}: {
  containerId: string;
  onPick: (primitiveId: string) => void;
}) {
  const [picking, setPicking] = useState(false);

  return (
    <div className="pane-empty" data-container-id={containerId}>
      <div className="pane-empty-eyebrow">Empty pane</div>
      <p className="pane-empty-hint">
        This pane has no contents yet. Pick what it should hold — you can change it later.
      </p>
      <button type="button" className="btn-primary btn-compact" onClick={() => setPicking(true)}>
        Choose a primitive
      </button>
      {/* The prompt stays put behind the dialog rather than being replaced by
          the list (557). The list is now long enough that an empty pane could
          not hold it, and a modal is not something the pane has to make room
          for. */}
      {picking ? (
        <PrimitivePickerModal
          legend="Choose a primitive"
          onClose={() => setPicking(false)}
          onPick={(primitiveId) => {
            setPicking(false);
            onPick(primitiveId);
          }}
        />
      ) : null}
    </div>
  );
}

export function ContainerPane({
  containerId,
  container,
}: {
  containerId: string;
  /** The container as the layout stores it: unvalidated, and possibly absent. */
  container: unknown;
}) {
  const slug = useSidebarWorkspaceSlug();
  // Outside the view route there is no id, and the write refuses to compose a
  // PATCH without one — a pane can only reach the screen underneath it.
  const { viewId = "" } = useParams<{ viewId: string }>();
  const pickPrimitive = useViewLayoutWrite(slug, viewId);

  const stored = asJson(container);
  const settings = paneSettings(container);

  if (typeof settings.primitive_id !== "string") {
    return (
      <EmptyContainerPrompt
        containerId={containerId}
        onPick={(primitiveId) => pickPrimitive(containerId, primitiveId)}
      />
    );
  }
  return (
    <ContainerPrimitiveHost
      containerId={containerId}
      settings={settings}
      kind={containerKindOf(stored?.kind)}
    />
  );
}

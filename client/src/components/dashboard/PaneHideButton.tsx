import { PANE_LABELS } from "../../lib/appTopbarConfig";
import type { PaneId } from "../../state/uiStore";
import { IconCloseButton } from "../IconCloseButton";

export function PaneHideButton({
  pane,
  onHide,
  disabled,
  className,
}: {
  pane: PaneId;
  onHide: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <IconCloseButton
      className={`pane-hide-btn${className ? ` ${className}` : ""}`}
      title={disabled ? "At least one pane must stay visible" : `Hide ${PANE_LABELS[pane]}`}
      aria-label={`Hide ${PANE_LABELS[pane]}`}
      disabled={disabled}
      onClick={onHide}
    />
  );
}

import { useUiStore } from "../state/uiStore";
import { AppActionBar } from "./AppActionBar";
import { CopilotDock } from "./CopilotDock";

/**
 * Chat/terminal panels with the global action bar beneath them, as one dockable
 * unit (bottom or right). The bar is always present; the panels only mount when
 * something is open, so the bar is what a collapsed dock looks like.
 */
export function AppUtilityDock() {
  const edge = useUiStore((s) => s.utilityDockEdge);
  const width = useUiStore((s) => s.copilotWidth);

  return (
    <div
      className={`app-utility-dock app-utility-dock--${edge}`}
      style={edge === "right" ? { width } : undefined}
    >
      <CopilotDock />
      <AppActionBar />
    </div>
  );
}

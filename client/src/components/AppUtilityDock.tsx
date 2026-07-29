import { useUiStore } from "../state/uiStore";
import { AppStatusBar } from "./AppStatusBar";
import { CopilotDock } from "./CopilotDock";

/**
 * Status strip + Copilot dock as one dockable unit (bottom or right).
 */
export function AppUtilityDock() {
  const edge = useUiStore((s) => s.utilityDockEdge);
  const width = useUiStore((s) => s.copilotWidth);

  return (
    <div
      className={`app-utility-dock app-utility-dock--${edge}`}
      style={edge === "right" ? { width } : undefined}
    >
      <AppStatusBar />
      <CopilotDock />
    </div>
  );
}

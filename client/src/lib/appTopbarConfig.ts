import type { PaneId } from "../state/uiStore";

export const PANE_LABELS: Record<PaneId, string> = {
  workspaces: "Workspaces",
  tickets: "Work items",
  workflow: "Workflow",
  artifacts: "Artifacts",
};

export const PANE_ORDER: PaneId[] = ["workspaces", "tickets", "workflow", "artifacts"];

import type { AppPage } from "./appNavigation";
import type { PaneId } from "../state/uiStore";

export const PANE_LABELS: Record<PaneId, string> = {
  workspaces: "Workspaces",
  tickets: "Work items",
  workflow: "Workflow",
  artifacts: "Artifacts",
};

export const TOOL_OPTIONS: { page: AppPage; label: string }[] = [
  { page: "dashboard", label: "IDE" },
  { page: "editor", label: "Editor" },
  { page: "branch-triage", label: "Branch Triage" },
  { page: "queue", label: "Parallel Execution" },
  { page: "studio", label: "Studios" },
];

export const PANE_ORDER: PaneId[] = ["workspaces", "tickets", "workflow", "artifacts"];

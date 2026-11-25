import type { LaunchPhase, LaunchState } from "../../api/reviewApi";
import type { StatusTone } from "../../lib/statusTone";
import { StatusChip } from "../ui/StatusChip";

const PHASE_CHIP: Record<LaunchPhase, { tone: StatusTone; label: string }> = {
  preparing: { tone: "run", label: "Preparing" },
  running: { tone: "run", label: "Running" },
  exited: { tone: "idle", label: "Exited" },
  failed: { tone: "bad", label: "Failed" },
  stopped: { tone: "idle", label: "Stopped" },
};

/** Where a review build's launch has got to. */
export function LaunchPhaseChip({ state }: { state: Pick<LaunchState, "phase" | "kind"> }) {
  // A check that exits at all passed; one that failed is `failed`.
  const passed = state.phase === "exited" && state.kind === "check";
  const { tone, label } = passed ? ({ tone: "done", label: "Passed" } as const) : PHASE_CHIP[state.phase];
  return <StatusChip tone={tone} label={label} />;
}

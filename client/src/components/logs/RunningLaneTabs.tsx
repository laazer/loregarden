import type { LedgerAttempt } from "../../api/types";

/** One tab per running lane; labels track live ledger status. */
export function RunningLaneTabs({
  lanes,
  selectedRunId,
  onSelect,
}: {
  lanes: LedgerAttempt[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  if (lanes.length === 0) return null;

  return (
    <div className="studio-subtabs running-lane-tabs" role="tablist">
      {lanes.map((lane, index) => {
        const isSelected = lane.run_id === selectedRunId;
        return (
          <button
            key={`${lane.run_id}-${index}`}
            type="button"
            role="tab"
            aria-selected={isSelected}
            className={`studio-subtab${isSelected ? " active" : ""}`}
            onClick={() => onSelect(lane.run_id)}
          >
            {lane.agent_id}
            {lane.skill_name ? ` · ${lane.skill_name}` : ""} · {lane.status}
          </button>
        );
      })}
    </div>
  );
}

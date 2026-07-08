import { useEffect, useMemo, useRef } from "react";

import type { TicketDetail } from "../../api/client";
import { DEFAULT_HIVE_SKIN, HIVE_SKIN_IDS, HIVE_SKINS, type HiveSkinId } from "../../lib/hive/skins";
import { agentStatusSnapshot, buildHiveWorld } from "../../lib/hive/worldModel";
import { useUiStore } from "../../state/uiStore";
import { HiveCssFloor } from "./hive/HiveCssFloor";
import "./HiveSimulationPanel.css";

function FloorIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.9">
      <path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6" />
    </svg>
  );
}

/**
 * Hive tab panel.
 * Intentionally CSS-only (no Pixi) so the Dashboard critical path cannot hang
 * on WebGL init. Pixi scene code remains under hive/scene for a later opt-in.
 */
export function HiveSimulationPanel({ ticket }: { ticket: TicketDetail }) {
  const hiveSkin = useUiStore((s) => s.hiveSkin);
  const setHiveSkin = useUiStore((s) => s.setHiveSkin);
  const prevStatusesRef = useRef<Record<string, import("../../api/client").StageStatus>>({});

  const model = useMemo(
    () =>
      buildHiveWorld(ticket.stages ?? [], {
        skin: hiveSkin || DEFAULT_HIVE_SKIN,
        hasErrorArtifact: Boolean(ticket.artifacts?.error || ticket.blocking_issues),
        previousStatuses: prevStatusesRef.current,
      }),
    [ticket.stages, ticket.artifacts?.error, ticket.blocking_issues, hiveSkin],
  );

  useEffect(() => {
    prevStatusesRef.current = agentStatusSnapshot(model.agents);
  }, [model.agents]);

  return (
    <div className="hive-panel">
      <div className="hive-panel__header">
        <FloorIcon />
        <span className="hive-panel__title">{model.floorTitle}</span>
        <span className="hive-panel__live">
          <span className="hive-panel__live-dot" aria-hidden />
          live
        </span>
        <div className="hive-panel__spacer" />
        <label className="hive-panel__skin">
          <span className="hive-panel__skin-label">Skin</span>
          <select
            className="hive-panel__skin-select"
            value={hiveSkin || DEFAULT_HIVE_SKIN}
            onChange={(e) => setHiveSkin(e.target.value as HiveSkinId)}
            aria-label="Hive simulation skin"
          >
            {HIVE_SKIN_IDS.map((id) => (
              <option key={id} value={id}>
                {HIVE_SKINS[id].label}
              </option>
            ))}
          </select>
        </label>
        <span className="hive-panel__legend">
          <span className="hive-panel__legend-dot hive-panel__legend-dot--working" aria-hidden />
          working
        </span>
        <span className="hive-panel__legend">
          <span className="hive-panel__legend-dot hive-panel__legend-dot--done" aria-hidden />
          done
        </span>
        <span className="hive-panel__legend">
          <span className="hive-panel__legend-dot hive-panel__legend-dot--idle" aria-hidden />
          idle
        </span>
      </div>

      <div className="hive-panel__floor">
        <HiveCssFloor model={model} />
        {model.idle ? (
          <div className="hive-panel__idle">
            <div className="hive-panel__idle-title">The floor is quiet</div>
            <div className="hive-panel__idle-copy">
              No agents assigned yet — advance this ticket to staff the workflow.
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

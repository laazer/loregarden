import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { StageStatus, TicketDetail, WorkflowStageView } from "../../api/client";
import { DEFAULT_HIVE_SKIN, HIVE_ENABLED_SKIN_IDS, HIVE_SKINS, resolveHiveSkinId, type HiveSkinId } from "../../lib/hive/skins";
import { buildHiveReplayFrames } from "../../lib/hive/replay";
import {
  HIVE_SPEED_MULTIPLIERS,
  hiveReplayFrameMs,
  hiveSpeedLabel,
} from "../../lib/hive/speed";
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
  const hiveSkin = resolveHiveSkinId(useUiStore((s) => s.hiveSkin));
  const setHiveSkin = useUiStore((s) => s.setHiveSkin);
  const hiveSpeedIndex = useUiStore((s) => s.hiveSpeedIndex);
  const stepHiveSpeed = useUiStore((s) => s.stepHiveSpeed);
  const speedMultiplier = HIVE_SPEED_MULTIPLIERS[hiveSpeedIndex] ?? HIVE_SPEED_MULTIPLIERS[1];
  const replayFrameMs = hiveReplayFrameMs(speedMultiplier);
  const prevStatusesRef = useRef<Record<string, StageStatus>>({});
  const [replayFrames, setReplayFrames] = useState<WorkflowStageView[][] | null>(null);
  const [replayIndex, setReplayIndex] = useState(0);
  const [replayNonce, setReplayNonce] = useState(0);
  const playing = replayFrames !== null;

  const liveStages = ticket.stages ?? [];
  const displayStages = playing ? (replayFrames[replayIndex] ?? liveStages) : liveStages;

  const model = useMemo(
    () =>
      buildHiveWorld(displayStages, {
        skin: hiveSkin || DEFAULT_HIVE_SKIN,
        hasErrorArtifact: !playing && Boolean(ticket.artifacts?.error || ticket.blocking_issues),
        previousStatuses: prevStatusesRef.current,
      }),
    [
      displayStages,
      ticket.artifacts?.error,
      ticket.blocking_issues,
      hiveSkin,
      playing,
      replayIndex,
      replayNonce,
    ],
  );

  useEffect(() => {
    prevStatusesRef.current = agentStatusSnapshot(model.agents);
  }, [model.agents]);

  const stopReplay = useCallback(() => {
    setReplayFrames(null);
    setReplayIndex(0);
    prevStatusesRef.current = {};
  }, []);

  const startReplay = useCallback(() => {
    const frames = buildHiveReplayFrames(liveStages);
    if (frames.length === 0) return;
    prevStatusesRef.current = {};
    setReplayFrames(frames);
    setReplayIndex(0);
    setReplayNonce((n) => n + 1);
  }, [liveStages]);

  useEffect(() => {
    if (!replayFrames) return;
    if (replayIndex >= replayFrames.length - 1) {
      const doneTimer = window.setTimeout(() => stopReplay(), replayFrameMs);
      return () => window.clearTimeout(doneTimer);
    }
    const timer = window.setTimeout(() => {
      setReplayIndex((i) => i + 1);
    }, replayFrameMs);
    return () => window.clearTimeout(timer);
  }, [replayFrames, replayIndex, stopReplay, replayFrameMs]);

  const canReplay = useMemo(() => buildHiveReplayFrames(liveStages).length > 0, [liveStages]);

  return (
    <div className={`hive-panel hive-panel--${hiveSkin || DEFAULT_HIVE_SKIN}`}>
      <div className="hive-panel__header">
        <FloorIcon />
        <span className="hive-panel__title">{model.floorTitle}</span>
        <span className={`hive-panel__live${playing ? " hive-panel__live--replay" : ""}`}>
          <span className="hive-panel__live-dot" aria-hidden />
          {playing ? `replay ${replayIndex + 1}/${replayFrames.length}` : "live"}
        </span>
        <div className="hive-panel__spacer" />
        <div className="hive-panel__speed" role="group" aria-label="Simulation speed">
          <button
            type="button"
            className="hive-panel__speed-btn"
            onClick={() => stepHiveSpeed(-1)}
            disabled={hiveSpeedIndex <= 0}
            aria-label="Slow down simulation"
          >
            −
          </button>
          <span className="hive-panel__speed-label" aria-live="polite">
            {hiveSpeedLabel(speedMultiplier)}
          </span>
          <button
            type="button"
            className="hive-panel__speed-btn"
            onClick={() => stepHiveSpeed(1)}
            disabled={hiveSpeedIndex >= HIVE_SPEED_MULTIPLIERS.length - 1}
            aria-label="Speed up simulation"
          >
            +
          </button>
        </div>
        <button
          type="button"
          className="hive-panel__replay"
          onClick={playing ? stopReplay : startReplay}
          disabled={!playing && !canReplay}
          aria-pressed={playing}
        >
          {playing ? "Stop" : "Replay"}
        </button>
        <label className="hive-panel__skin">
          <span className="hive-panel__skin-label">Skin</span>
          {HIVE_ENABLED_SKIN_IDS.length > 1 ? (
            <select
              className="hive-panel__skin-select"
              value={hiveSkin || DEFAULT_HIVE_SKIN}
              onChange={(e) => setHiveSkin(e.target.value as HiveSkinId)}
              aria-label="Hive simulation skin"
            >
              {HIVE_ENABLED_SKIN_IDS.map((id) => (
                <option key={id} value={id}>
                  {HIVE_SKINS[id].label}
                </option>
              ))}
            </select>
          ) : (
            <span className="hive-panel__skin-value">{HIVE_SKINS[hiveSkin].label}</span>
          )}
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
        <HiveCssFloor
          key={`floor-${replayNonce}-${hiveSkin}`}
          model={model}
          speedMultiplier={speedMultiplier}
        />
        {model.idle && !playing ? (
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

import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useState } from "react";

import type {
  RuntimeOptions,
  TicketDetail,
  WorkflowStageView,
  WorkspaceRuntimeSettings,
} from "../api/client";
import { LanePicker, type LaneChoice } from "./LanePicker";
import {
  WorkspaceRuntimeFields,
  runtimeSettingsEqual,
} from "./WorkspaceRuntimeFields";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";

export interface AgentsAssembleOptions {
  runtime: WorkspaceRuntimeSettings;
  stopAtStageKey: string;
  autoApprove: boolean;
  branch: string;
  /**
   * Which execution lane to run in; null asks for whichever is quietest. Every
   * start takes a slot now, so this dialog has to say which one.
   */
  slotNumber: LaneChoice;
  /** Max seconds each agent run may take; undefined keeps the agent default. */
  timeoutSeconds?: number;
}

interface AgentsAssembleModalProps {
  open: boolean;
  ticket: TicketDetail | null;
  workspaceRuntime: WorkspaceRuntimeSettings;
  runtimeOptions: RuntimeOptions | undefined;
  stages: WorkflowStageView[];
  isRunning: boolean;
  isSavingRuntime: boolean;
  /**
   * Lane to preselect. The board opens this dialog from a specific lane, so it
   * starts there rather than making you pick the lane you just clicked.
   */
  defaultSlotNumber?: LaneChoice;
  onClose: () => void;
  onConfirm: (options: AgentsAssembleOptions) => void | Promise<void>;
}

export function AgentsAssembleModal({
  open,
  ticket,
  workspaceRuntime,
  runtimeOptions,
  stages,
  isRunning,
  isSavingRuntime,
  defaultSlotNumber = null,
  onClose,
  onConfirm,
}: AgentsAssembleModalProps) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [draftRuntime, setDraftRuntime] = useState(workspaceRuntime);
  const [stopAtStageKey, setStopAtStageKey] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [branch, setBranch] = useState("");
  const [slotNumber, setSlotNumber] = useState<LaneChoice>(defaultSlotNumber);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");

  useEffect(() => {
    if (!open || !ticket) return;
    setDraftRuntime(workspaceRuntime);
    setStopAtStageKey("");
    setAutoApprove(false);
    setBranch(ticket.branch || `loregarden/${ticket.external_id}`);
    setSlotNumber(defaultSlotNumber);
    setTimeoutSeconds("");
  }, [open, ticket, workspaceRuntime, defaultSlotNumber]);

  if (!open || !ticket) return null;

  const busy = isRunning || isSavingRuntime;
  const runnableStages = stages.filter((s) => s.key !== "done");
  const runtimeDirty = !runtimeSettingsEqual(draftRuntime, workspaceRuntime);
  // A parent ticket runs no stages of its own (its children carry the work), so it
  // never checks out a branch — hide the field and don't gate starting on it.
  const isParent = (ticket.child_count ?? 0) > 0;

  return (
    <>
      <div className="modal-overlay" onClick={busy ? undefined : onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agents-assemble-title"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Orchestration</div>
            <h2 id="agents-assemble-title" className="modal-title">
              Agents Assemble
            </h2>
            <p className="modal-subtitle">{ticket.title}</p>
          </div>
          <IconCloseButton disabled={busy} onClick={onClose} />
        </div>

        <div className="modal-body">
          {!isParent && (
            <div style={{ marginBottom: 16 }}>
              <div className="modal-section-title">Branch</div>
              <input
                className="btn-secondary"
                style={{ width: "100%", fontSize: 12, boxSizing: "border-box" }}
                value={branch}
                disabled={busy}
                onChange={(e) => setBranch(e.target.value)}
                placeholder={`loregarden/${ticket.external_id}`}
              />
              <p className="modal-hint" style={{ marginTop: 6 }}>
                Agent runs checkout this branch before executing.
              </p>
            </div>
          )}

          {runtimeOptions && (
            <div style={{ marginBottom: 16 }}>
              <div className="modal-section-title">Model for this run</div>
              <WorkspaceRuntimeFields
                runtime={draftRuntime}
                options={runtimeOptions}
                disabled={busy}
                compact
                onChange={setDraftRuntime}
              />
              {runtimeDirty && (
                <p className="modal-hint" style={{ marginTop: 8 }}>
                  Runtime changes will be saved when you start orchestration.
                </p>
              )}
            </div>
          )}

          <div style={{ marginBottom: 16 }}>
            <div className="modal-section-title">Stop at stage (optional)</div>
            <select
              className="btn-secondary"
              style={{ width: "100%", fontSize: 12 }}
              value={stopAtStageKey}
              disabled={busy}
              onChange={(e) => setStopAtStageKey(e.target.value)}
            >
              <option value="">Run until blocked or complete</option>
              {runnableStages.map((stage) => (
                <option key={stage.key} value={stage.key}>
                  {stage.name} ({stage.key})
                </option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: 16 }}>
            <LanePicker
              value={slotNumber}
              onChange={setSlotNumber}
              enabled={open}
              disabled={busy}
            />
          </div>

          <div style={{ marginBottom: 16 }}>
            <div className="modal-section-title">Max agent runtime (seconds, optional)</div>
            <input
              type="number"
              min={30}
              className="btn-secondary"
              style={{ width: 140, fontSize: 12, boxSizing: "border-box" }}
              value={timeoutSeconds}
              disabled={busy}
              onChange={(e) => setTimeoutSeconds(e.target.value)}
              placeholder="Agent default"
            />
            <p className="modal-hint" style={{ marginTop: 6 }}>
              Applies to every agent run for this ticket and its child tickets.
            </p>
          </div>

          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 13,
              color: "var(--txm)",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={autoApprove}
              disabled={busy}
              onChange={(e) => setAutoApprove(e.target.checked)}
            />
            Auto-approve CLI tool permissions and workflow gates for this ticket and its subtree
          </label>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || (!isParent && !branch.trim())}
            onClick={() => {
              const parsedTimeout = timeoutSeconds.trim()
                ? Number(timeoutSeconds)
                : undefined;
              void onConfirm({
                runtime: draftRuntime,
                stopAtStageKey,
                autoApprove,
                // A parent's branch is unused; pass its stored value so confirmAssemble
                // treats it as unchanged and never rewrites it.
                branch: isParent ? (ticket.branch ?? "") : branch.trim(),
                slotNumber,
                timeoutSeconds:
                  parsedTimeout !== undefined && Number.isFinite(parsedTimeout)
                    ? parsedTimeout
                    : undefined,
              });
            }}
          >
            {isSavingRuntime ? "Saving…" : isRunning ? "Starting…" : "Start orchestration"}
          </button>
        </div>
      </div>
    </>
  );
}

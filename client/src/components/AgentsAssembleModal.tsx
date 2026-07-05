import { useEffect, useState } from "react";

import type {
  RuntimeOptions,
  TicketDetail,
  WorkflowStageView,
  WorkspaceRuntimeSettings,
} from "../api/client";
import {
  WorkspaceRuntimeFields,
  runtimeSettingsEqual,
} from "./WorkspaceRuntimeFields";

export interface AgentsAssembleOptions {
  runtime: WorkspaceRuntimeSettings;
  stopAtStageKey: string;
  autoApprove: boolean;
  branch: string;
}

interface AgentsAssembleModalProps {
  open: boolean;
  ticket: TicketDetail | null;
  workspaceRuntime: WorkspaceRuntimeSettings;
  runtimeOptions: RuntimeOptions | undefined;
  stages: WorkflowStageView[];
  isRunning: boolean;
  isSavingRuntime: boolean;
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
  onClose,
  onConfirm,
}: AgentsAssembleModalProps) {
  const [draftRuntime, setDraftRuntime] = useState(workspaceRuntime);
  const [stopAtStageKey, setStopAtStageKey] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [branch, setBranch] = useState("");

  useEffect(() => {
    if (!open || !ticket) return;
    setDraftRuntime(workspaceRuntime);
    setStopAtStageKey("");
    setAutoApprove(false);
    setBranch(ticket.branch || `loregarden/${ticket.external_id}`);
  }, [open, ticket, workspaceRuntime]);

  if (!open || !ticket) return null;

  const busy = isRunning || isSavingRuntime;
  const runnableStages = stages.filter((s) => s.key !== "done");
  const runtimeDirty = !runtimeSettingsEqual(draftRuntime, workspaceRuntime);

  return (
    <>
      <div className="modal-overlay" onClick={busy ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="agents-assemble-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Orchestration</div>
            <h2 id="agents-assemble-title" className="modal-title">
              Agents Assemble
            </h2>
            <p className="modal-subtitle">{ticket.title}</p>
          </div>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
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
            Auto-approve CLI tool permissions during this run
          </label>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !branch.trim()}
            onClick={() =>
              void onConfirm({
                runtime: draftRuntime,
                stopAtStageKey,
                autoApprove,
                branch: branch.trim(),
              })
            }
          >
            {isSavingRuntime ? "Saving…" : isRunning ? "Starting…" : "Start orchestration"}
          </button>
        </div>
      </div>
    </>
  );
}

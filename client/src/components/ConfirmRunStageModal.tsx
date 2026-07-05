import { useEffect, useState } from "react";

import type { RuntimeOptions, TicketDetail, WorkflowStageView, WorkspaceRuntimeSettings } from "../api/client";
import {
  WorkspaceRuntimeFields,
  runtimeSettingsEqual,
} from "./WorkspaceRuntimeFields";

interface ConfirmRunStageModalProps {
  open: boolean;
  ticket: TicketDetail | null;
  stage: WorkflowStageView | null;
  workspaceSlug: string;
  workspaceRuntime: WorkspaceRuntimeSettings;
  runtimeOptions: RuntimeOptions | undefined;
  isRunning: boolean;
  isSavingRuntime: boolean;
  onClose: () => void;
  onConfirm: (runtime: WorkspaceRuntimeSettings) => void | Promise<void>;
}

export function ConfirmRunStageModal({
  open,
  ticket,
  stage,
  workspaceSlug: _workspaceSlug,
  workspaceRuntime,
  runtimeOptions,
  isRunning,
  isSavingRuntime,
  onClose,
  onConfirm,
}: ConfirmRunStageModalProps) {
  const [draftRuntime, setDraftRuntime] = useState(workspaceRuntime);

  useEffect(() => {
    if (!open) return;
    setDraftRuntime(workspaceRuntime);
  }, [open, workspaceRuntime, stage?.key]);

  if (!open || !ticket || !stage) return null;

  const isRerun = stage.status === "done";
  const humanGate = isHumanGateStage(stage);
  const doneStage = isDoneStage(stage);
  const busy = isRunning || isSavingRuntime;
  const runtimeDirty = !runtimeSettingsEqual(draftRuntime, workspaceRuntime);

  const handleConfirm = () => {
    void onConfirm(draftRuntime);
  };

  return (
    <>
      <div className="modal-overlay" onClick={busy ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="confirm-run-stage-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Stage execution</div>
            <h2 id="confirm-run-stage-title" className="modal-title">
              {doneStage
                ? "Complete ticket?"
                : humanGate
                  ? isRerun
                    ? "Re-request approval?"
                    : "Request approval?"
                  : isRerun
                    ? "Re-run stage?"
                    : "Run stage?"}
            </h2>
            <p className="modal-subtitle">{ticket.title}</p>
          </div>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
            {doneStage ? (
              <>
                Mark <strong style={{ color: "var(--tx)" }}>{ticket.title}</strong> complete? This
                closes the workflow — no agent is invoked.
              </>
            ) : humanGate ? (
              isRerun ? (
                <>
                  Re-open human approval for{" "}
                  <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>? A new inbox item will
                  be created for sign-off.
                </>
              ) : (
                <>
                  Request human sign-off for{" "}
                  <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>. This creates an inbox
                  approval — no agent CLI is invoked.
                </>
              )
            ) : isRerun ? (
              <>
                Are you sure you want to re-run <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>?
                This stage is already marked done and will invoke its sub-agent again.
              </>
            ) : (
              <>
                Are you sure you want to run <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>?
                This will invoke the stage sub-agent and may update ticket workflow state.
              </>
            )}
          </p>
          {(stage.agent_id || stage.skill_name) && (
            <div className="state-card" style={{ marginTop: 4 }}>
              <div className="state-label">Agent</div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {stage.agent_id}
                {stage.skill_name ? ` · ${stage.skill_name}` : ""}
              </div>
            </div>
          )}

          {runtimeOptions && !humanGate && !doneStage && (
            <div style={{ marginTop: 8 }}>
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
                  Runtime changes will be saved when you confirm this run.
                </p>
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy} onClick={handleConfirm}>
            {isSavingRuntime
              ? "Saving…"
              : isRunning
                ? doneStage
                  ? "Completing…"
                  : humanGate
                    ? "Requesting…"
                    : "Running…"
                : doneStage
                  ? "Complete ticket"
                  : humanGate
                    ? isRerun
                      ? "Re-request approval"
                      : "Request approval"
                    : isRerun
                      ? "Re-run stage"
                      : "Run stage"}
          </button>
        </div>
      </div>
    </>
  );
}

export function isHumanGateStage(stage: WorkflowStageView): boolean {
  return !stage.agent_id?.trim() && stage.key !== "done";
}

export function isDoneStage(stage: WorkflowStageView): boolean {
  return stage.key === "done";
}

export function stageRunButtonLabel(stage: WorkflowStageView, isRunning: boolean): string {
  if (isRunning) return "Running…";
  if (isDoneStage(stage)) {
    if (stage.status === "done") return "Complete";
    return "Complete ticket";
  }
  if (isHumanGateStage(stage)) {
    if (stage.status === "awaiting") return "Awaiting approval";
    if (stage.status === "done") return "Re-request";
    return "Request approval";
  }
  if (stage.status === "done") return "Re-Run";
  return "Run";
}

export function currentStageRunLabel(stage: WorkflowStageView | undefined, isRunning: boolean): string {
  if (!stage) return "Run current stage";
  if (isRunning) return "Running…";
  if (isDoneStage(stage)) {
    if (stage.status === "done") return "Ticket complete";
    return "Complete ticket";
  }
  if (isHumanGateStage(stage)) {
    if (stage.status === "awaiting") return "Awaiting approval";
    if (stage.status === "done") return "Re-request approval";
    return "Request approval";
  }
  if (stage.status === "done") return "Re-run current stage";
  return "Run current stage";
}

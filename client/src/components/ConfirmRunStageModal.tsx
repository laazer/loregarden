import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useState } from "react";

import type { RuntimeOptions, TicketDetail, WorkflowStageView, WorkspaceRuntimeSettings } from "../api/client";
import {
  isClassifyStage,
  isDoneStage,
  isHumanGateStage,
  isParallelStage,
  stageAgentSubtitle,
} from "../lib/stageDisplay";
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
  isOpeningPr?: boolean;
  onClose: () => void;
  onConfirm: (runtime: WorkspaceRuntimeSettings) => void | Promise<void>;
  onOpenPr?: () => void;
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
  isOpeningPr = false,
  onClose,
  onConfirm,
  onOpenPr,
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
  const parallelStage = isParallelStage(stage);
  const classifyStage = isClassifyStage(stage);
  const busy = isRunning || isSavingRuntime || isOpeningPr;
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
          <IconCloseButton disabled={busy} onClick={onClose} />
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
            ) : parallelStage ? (
              isRerun ? (
                <>
                  Re-run parallel review for{" "}
                  <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>? All configured
                  reviewers will run concurrently.
                </>
              ) : (
                <>
                  Run parallel review for{" "}
                  <strong style={{ color: "var(--tx)" }}>{stage.name}</strong>? All configured
                  reviewers will run concurrently.
                </>
              )
            ) : classifyStage ? (
              <>
                Run <strong style={{ color: "var(--tx)" }}>{stage.name}</strong> using the ticket&apos;s{" "}
                <code>next_agent</code> when set, otherwise keyword routing applies.
              </>
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
          {(stageAgentSubtitle(stage) || stage.agent_id || stage.skill_name) && (
            <div className="state-card" style={{ marginTop: 4 }}>
              <div className="state-label">{parallelStage ? "Reviewers" : classifyStage ? "Routes" : "Agent"}</div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {stageAgentSubtitle(stage) || `${stage.agent_id}${stage.skill_name ? ` · ${stage.skill_name}` : ""}`}
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
          {humanGate && onOpenPr && (
            <button type="button" className="btn-secondary" disabled={busy} onClick={onOpenPr}>
              {isOpeningPr ? "Opening PR…" : "Open PR"}
            </button>
          )}
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
                      : "Approve"
                    : parallelStage
                  ? isRerun
                    ? "Re-run reviews"
                    : "Run reviews"
                  : isRerun
                    ? "Re-run stage"
                    : "Run stage"}
          </button>
        </div>
      </div>
    </>
  );
}

import { useEffect, useState } from "react";

import type { Approval } from "../api/client";
import { IconCloseButton } from "./IconCloseButton";

export interface RejectApprovalPayload {
  response: string;
  route_to_stage_key?: string;
}

interface RejectApprovalModalProps {
  open: boolean;
  approval: Approval | null;
  isSubmitting?: boolean;
  onClose: () => void;
  onConfirm: (payload: RejectApprovalPayload) => void;
}

export function RejectApprovalModal({
  open,
  approval,
  isSubmitting,
  onClose,
  onConfirm,
}: RejectApprovalModalProps) {
  const [reason, setReason] = useState("");
  const [routeStageKey, setRouteStageKey] = useState("");

  useEffect(() => {
    if (!open) return;
    setReason("");
    setRouteStageKey("");
  }, [open, approval?.id]);

  if (!open || !approval) return null;

  const routeOptions = approval.route_options ?? [];
  const canSubmit = reason.trim().length > 0;

  const handleConfirm = () => {
    if (!canSubmit) return;
    onConfirm({
      response: reason.trim(),
      route_to_stage_key: routeStageKey || undefined,
    });
  };

  return (
    <>
      <div
        className="modal-overlay"
        onClick={isSubmitting ? undefined : onClose}
        role="presentation"
      />
      <div className="modal-panel" role="dialog" aria-labelledby="reject-approval-title">
        <div className="modal-header">
          <div>
            <div className="state-label">{approval.stage_name}</div>
            <h2 id="reject-approval-title" className="modal-title">
              Reject sign-off?
            </h2>
            <p className="modal-subtitle">{approval.title}</p>
          </div>
          <IconCloseButton disabled={isSubmitting} onClick={onClose} />
        </div>

        <div className="modal-body">
          <div style={{ marginBottom: 16 }}>
            <div className="modal-section-title">Reason</div>
            <textarea
              value={reason}
              disabled={isSubmitting}
              onChange={(e) => setReason(e.target.value)}
              placeholder="What needs to change before this can pass?"
              rows={4}
              autoFocus
              style={{
                width: "100%",
                padding: "8px 10px",
                borderRadius: 8,
                border: "1px solid var(--bd)",
                background: "var(--bg2)",
                color: "var(--tx)",
                fontSize: 12,
                resize: "vertical",
                boxSizing: "border-box",
              }}
            />
            <p className="modal-hint" style={{ marginTop: 6 }}>
              Shared with the agent that picks this back up as blocking context.
            </p>
          </div>

          {routeOptions.length > 0 && (
            <div>
              <div className="modal-section-title">Route back to stage (optional)</div>
              <select
                className="btn-secondary"
                style={{ width: "100%", fontSize: 12 }}
                value={routeStageKey}
                disabled={isSubmitting}
                onChange={(e) => setRouteStageKey(e.target.value)}
              >
                <option value="">Use the workflow's default reject routing</option>
                {routeOptions.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.name}
                  </option>
                ))}
              </select>
              <p className="modal-hint" style={{ marginTop: 6 }}>
                Leave unset to follow the template's configured reject route.
              </p>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isSubmitting} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            style={{ background: "var(--rdl, #ff6a54)", borderColor: "transparent" }}
            disabled={!canSubmit || isSubmitting}
            onClick={handleConfirm}
          >
            {isSubmitting ? "Rejecting…" : "Reject"}
          </button>
        </div>
      </div>
    </>
  );
}

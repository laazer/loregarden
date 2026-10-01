import { useQueryClient } from "@tanstack/react-query";

import type { TicketDetail, WorkflowTemplateSummary, WorkspaceWorkflow } from "../../api/client";
import { STATE_COLORS, STATE_LABELS } from "../UpdateStateModal";

interface WorkflowTicketSettingsProps {
  ticket: TicketDetail;
  workflowTemplates: WorkflowTemplateSummary[] | undefined;
  /** The ticket's workspace default, to say when the ticket overrides it. */
  workspaceWorkflow: WorkspaceWorkflow | undefined;
  workflowBusy: boolean;
  templatePending: boolean;
  postureSaving: boolean;
  onWorkflowChange: (template: string) => void;
  onSaveBranch: (branch: string) => void;
  onPostureChange: (posture: string) => void;
}

/**
 * A leaf ticket's settings in the Console's workflow pane: its workflow
 * template, branch, state and stage, and compatibility posture.
 */
export function WorkflowTicketSettings({
  ticket,
  workflowTemplates,
  workspaceWorkflow,
  workflowBusy,
  templatePending,
  postureSaving,
  onWorkflowChange,
  onSaveBranch,
  onPostureChange,
}: WorkflowTicketSettingsProps) {
  const qc = useQueryClient();
  return (
    <>
      {workflowTemplates && workflowTemplates.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div className="state-label" style={{ marginBottom: 6 }}>
            Workflow template
          </div>
          <select
            className="btn-secondary"
            style={{ width: "100%", maxWidth: 360, fontSize: 12 }}
            value={ticket.workflow_template_slug || ""}
            disabled={workflowBusy || templatePending}
            onChange={(e) => {
              if (e.target.value === ticket.workflow_template_slug) return;
              onWorkflowChange(e.target.value);
            }}
          >
            <option value="">No workflow</option>
            {workflowTemplates.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.name} ({t.stage_count} stages)
              </option>
            ))}
          </select>
          {ticket.workflow_template_slug &&
            workspaceWorkflow?.template_slug &&
            ticket.workflow_template_slug !== workspaceWorkflow.template_slug && (
              <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
                Workspace default: {workspaceWorkflow.template_name}
              </div>
            )}
        </div>
      )}
      <div style={{ marginBottom: 16 }}>
        <div className="state-label" style={{ marginBottom: 6 }}>
          Branch
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", maxWidth: 360 }}>
          <input
            className="btn-secondary"
            style={{ flex: 1, minWidth: 0, fontSize: 12, boxSizing: "border-box" }}
            value={ticket.branch || ""}
            placeholder={`loregarden/${ticket.external_id}`}
            onChange={(e) => {
              qc.setQueryData(["ticket", ticket.id], (current: TicketDetail | undefined) =>
                current ? { ...current, branch: e.target.value } : current,
              );
            }}
            onBlur={(e) => {
              if (e.target.value === (ticket.branch ?? "")) return;
              onSaveBranch(e.target.value.trim());
            }}
          />
          <button
            type="button"
            className="btn-secondary btn-compact"
            title="Set branch to main"
            onClick={() => {
              qc.setQueryData(["ticket", ticket.id], (current: TicketDetail | undefined) =>
                current ? { ...current, branch: "main" } : current,
              );
              if ((ticket.branch ?? "") !== "main") {
                onSaveBranch("main");
              }
            }}
          >
            Use main
          </button>
        </div>
      </div>
      <div className="dual-state">
        <div className="state-card">
          <div className="state-label">Ticket state · WHAT</div>
          <div style={{ fontWeight: 600, color: STATE_COLORS[ticket.state] }}>
            {STATE_LABELS[ticket.state]}
          </div>
          {ticket.state_locked && (
            <span className="count-pill" style={{ marginTop: 8, fontSize: 10 }}>
              locked
            </span>
          )}
        </div>
        <div className="state-card">
          <div className="state-label">Workflow · HOW</div>
          <div style={{ fontWeight: 600 }}>
            {ticket.workflow_stage_name || ticket.workflow_stage_key || "—"}
          </div>
          <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 4 }}>
            {ticket.workflow_stage_status.replace("_", " ")}
          </div>
          {ticket.current_stage_agent?.trim() && (
            <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txm)", marginTop: 6 }}>
              next agent · {ticket.current_stage_agent}
            </div>
          )}
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <div className="state-label" style={{ marginBottom: 6 }}>
          Compatibility posture · HOW FREELY
        </div>
        <select
          className="btn-secondary"
          style={{ width: "100%", maxWidth: 360, fontSize: 12 }}
          value={ticket.compatibility_posture || ""}
          disabled={postureSaving}
          onChange={(e) => {
            if (e.target.value === (ticket.compatibility_posture || "")) return;
            onPostureChange(e.target.value);
          }}
        >
          <option value="">Inherit</option>
          <option value="greenfield">greenfield — no consumers; delete and rename freely</option>
          <option value="internal">internal — break freely, but migrate every caller</option>
          <option value="public">public — external consumers; preserve and deprecate</option>
        </select>
        {/* An inherited value is meaningless without its origin — always show which
            milestone/feature/workspace the agent will actually be told. */}
        <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
          Agents are told: <strong>{ticket.resolved_compatibility_posture || "—"}</strong>
          {ticket.compatibility_posture_source ? ` · ${ticket.compatibility_posture_source}` : ""}
        </div>
      </div>
    </>
  );
}

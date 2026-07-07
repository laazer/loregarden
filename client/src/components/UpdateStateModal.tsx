import { useEffect, useMemo, useState } from "react";

import { IconCloseButton } from "./IconCloseButton";

import type { StageStatus, TicketDetail, TicketState } from "../api/client";

export const TICKET_STATES: TicketState[] = [
  "backlog",
  "in_progress",
  "blocked",
  "done",
  "wont_do",
];

export const STATE_COLORS: Record<TicketState, string> = {
  backlog: "var(--txm)",
  in_progress: "var(--blue)",
  blocked: "var(--red)",
  done: "var(--grn)",
  wont_do: "var(--amb)",
};

export const STATE_LABELS: Record<TicketState, string> = {
  backlog: "Backlog",
  in_progress: "In Progress",
  blocked: "Blocked",
  done: "Done",
  wont_do: "Won't do",
};

const STAGE_STATUSES: StageStatus[] = [
  "pending",
  "running",
  "blocked",
  "awaiting",
  "done",
  "wont_do",
];

export interface StateUpdateDraft {
  state: TicketState;
  stateLocked: boolean;
  workflowStageKey: string;
  workflowStageStatus: StageStatus;
  stageStatuses: Record<string, StageStatus>;
}

interface WorkflowStageOption {
  key: string;
  name: string;
}

interface UpdateStateModalProps {
  open: boolean;
  ticket: TicketDetail | null;
  workflowStages: WorkflowStageOption[];
  isSaving: boolean;
  onClose: () => void;
  onSave: (draft: StateUpdateDraft, original: StateUpdateDraft) => Promise<void>;
}

function draftFromTicket(ticket: TicketDetail): StateUpdateDraft {
  return {
    state: ticket.state,
    stateLocked: ticket.state_locked,
    workflowStageKey: ticket.workflow_stage_key,
    workflowStageStatus: ticket.workflow_stage_status,
    stageStatuses: Object.fromEntries(ticket.stages.map((s) => [s.key, s.status])),
  };
}

function draftsEqual(a: StateUpdateDraft, b: StateUpdateDraft): boolean {
  return (
    a.state === b.state &&
    a.stateLocked === b.stateLocked &&
    a.workflowStageKey === b.workflowStageKey &&
    a.workflowStageStatus === b.workflowStageStatus &&
    JSON.stringify(a.stageStatuses) === JSON.stringify(b.stageStatuses)
  );
}

function setAllStageStatuses(
  stageKeys: string[],
  status: StageStatus,
  current: Record<string, StageStatus>,
): Record<string, StageStatus> {
  const next = { ...current };
  for (const key of stageKeys) next[key] = status;
  return next;
}

function markThroughStage(
  orderedKeys: string[],
  throughKey: string,
  doneStatus: StageStatus,
  pendingStatus: StageStatus,
  current: Record<string, StageStatus>,
): Record<string, StageStatus> {
  const next = { ...current };
  const throughIdx = orderedKeys.indexOf(throughKey);
  orderedKeys.forEach((key, i) => {
    next[key] = throughIdx >= 0 && i <= throughIdx ? doneStatus : pendingStatus;
  });
  return next;
}

function setSelectedStageStatuses(
  stageKeys: string[],
  status: StageStatus,
  current: Record<string, StageStatus>,
): Record<string, StageStatus> {
  return setAllStageStatuses(stageKeys, status, current);
}

export function UpdateStateModal({
  open,
  ticket,
  workflowStages,
  isSaving,
  onClose,
  onSave,
}: UpdateStateModalProps) {
  const original = useMemo(() => (ticket ? draftFromTicket(ticket) : null), [ticket]);
  const [draft, setDraft] = useState<StateUpdateDraft | null>(original);
  const [bulkStatus, setBulkStatus] = useState<StageStatus>("pending");
  const [selectedStageKeys, setSelectedStageKeys] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (open && ticket) {
      setDraft(draftFromTicket(ticket));
      setBulkStatus("pending");
      setSelectedStageKeys(new Set());
    }
  }, [open, ticket]);

  if (!open || !ticket || !draft || !original) return null;

  const stageOptions = workflowStages.length
    ? workflowStages
    : ticket.stages.map((s) => ({ key: s.key, name: s.name }));

  const orderedStageKeys = ticket.stages.map((s) => s.key);
  const dirty = !draftsEqual(draft, original);

  const applyStageStatuses = (stageStatuses: Record<string, StageStatus>) => {
    setDraft((d) => d && { ...d, stageStatuses });
  };

  const toggleStageSelected = (key: string) => {
    setSelectedStageKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAllStages = () => setSelectedStageKeys(new Set(orderedStageKeys));
  const clearStageSelection = () => setSelectedStageKeys(new Set());

  const applyToSelected = (status: StageStatus) => {
    if (selectedStageKeys.size === 0) return;
    applyStageStatuses(
      setSelectedStageStatuses([...selectedStageKeys], status, draft.stageStatuses),
    );
  };

  const selectedCount = selectedStageKeys.size;

  return (
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="update-state-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Manual update</div>
            <h2 id="update-state-title" className="modal-title">
              Update state
            </h2>
            <p className="modal-subtitle">{ticket.title}</p>
          </div>
          <IconCloseButton onClick={onClose} />
        </div>

        <div className="modal-body">
          <section className="modal-section">
            <h3 className="modal-section-title">Ticket state · WHAT</h3>
            <span className="modal-field-label">Pick a status</span>
            <div className="modal-state-grid" role="radiogroup" aria-label="Ticket state">
              {TICKET_STATES.map((state) => {
                const active = draft.state === state;
                return (
                  <label
                    key={state}
                    className={`modal-state-option${active ? " modal-state-option-active" : ""}${
                      state === "wont_do" ? " modal-state-option-wont-do" : ""
                    }`}
                  >
                    <input
                      type="radio"
                      name="ticket-state"
                      value={state}
                      checked={active}
                      disabled={isSaving}
                      onChange={() =>
                        setDraft((d) =>
                          d
                            ? {
                                ...d,
                                state,
                                stateLocked: state === "wont_do" ? true : d.stateLocked,
                              }
                            : d,
                        )
                      }
                    />
                    <span style={{ color: active ? STATE_COLORS[state] : undefined }}>
                      {STATE_LABELS[state]}
                    </span>
                  </label>
                );
              })}
            </div>
            <label className="state-lock-toggle">
              <input
                type="checkbox"
                checked={draft.stateLocked}
                disabled={isSaving || draft.state === "wont_do"}
                onChange={(e) =>
                  setDraft((d) => d && { ...d, stateLocked: e.target.checked })
                }
              />
              Lock state (skip auto-sync from workflow)
              {draft.state === "wont_do" && (
                <span className="modal-hint"> — always locked for won't do</span>
              )}
            </label>
          </section>

          <section className="modal-section">
            <h3 className="modal-section-title">Workflow cursor · HOW</h3>
            <label className="modal-field">
              <span className="modal-field-label">Current stage</span>
              <select
                className="filter-select"
                value={draft.workflowStageKey}
                disabled={isSaving || !stageOptions.length}
                onChange={(e) =>
                  setDraft((d) => d && { ...d, workflowStageKey: e.target.value })
                }
              >
                {stageOptions.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="modal-field">
              <span className="modal-field-label">Stage status</span>
              <select
                className="filter-select"
                value={draft.workflowStageStatus}
                disabled={isSaving}
                onChange={(e) =>
                  setDraft(
                    (d) => d && { ...d, workflowStageStatus: e.target.value as StageStatus },
                  )
                }
              >
                {STAGE_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
          </section>

          <section className="modal-section">
            <div className="modal-section-header">
              <h3 className="modal-section-title">Lifecycle steps</h3>
            </div>
            <div className="modal-bulk-actions">
              <div className="modal-bulk-header">
                <span className="modal-field-label">Bulk update stages</span>
                <span className="modal-selection-count">
                  {selectedCount > 0 ? `${selectedCount} selected` : "Check steps below"}
                </span>
              </div>
              <div className="modal-bulk-row">
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isSaving}
                  onClick={selectAllStages}
                >
                  Select all
                </button>
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isSaving || selectedCount === 0}
                  onClick={clearStageSelection}
                >
                  Clear
                </button>
              </div>
              <div className="modal-bulk-row">
                <select
                  className="filter-select"
                  value={bulkStatus}
                  disabled={isSaving}
                  onChange={(e) => setBulkStatus(e.target.value as StageStatus)}
                >
                  {STAGE_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s.replace("_", " ")}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isSaving || selectedCount === 0}
                  onClick={() => applyToSelected(bulkStatus)}
                >
                  Apply to selected
                </button>
              </div>
              <div className="modal-bulk-row modal-bulk-presets">
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isSaving}
                  onClick={() =>
                    applyStageStatuses(
                      setAllStageStatuses(orderedStageKeys, "pending", draft.stageStatuses),
                    )
                  }
                >
                  All pending
                </button>
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isSaving}
                  onClick={() =>
                    applyStageStatuses(
                      setAllStageStatuses(orderedStageKeys, "done", draft.stageStatuses),
                    )
                  }
                >
                  All done
                </button>
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isSaving || !draft.workflowStageKey}
                  onClick={() =>
                    applyStageStatuses(
                      markThroughStage(
                        orderedStageKeys,
                        draft.workflowStageKey,
                        "done",
                        "pending",
                        draft.stageStatuses,
                      ),
                    )
                  }
                >
                  Done through cursor
                </button>
              </div>
            </div>
            <div className="modal-stage-list">
              {ticket.stages.map((stage) => {
                const isSelected = selectedStageKeys.has(stage.key);
                return (
                  <div
                    key={stage.key}
                    className={`modal-stage-row${isSelected ? " modal-stage-row-selected" : ""}`}
                  >
                    <label className="modal-stage-check">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={isSaving}
                        onChange={() => toggleStageSelected(stage.key)}
                      />
                    </label>
                    <span className="modal-stage-name">
                      {stage.name}
                      {stage.optional && <span className="count-pill">optional</span>}
                    </span>
                    <select
                      className="filter-select stage-status-select"
                      value={draft.stageStatuses[stage.key] ?? stage.status}
                      disabled={isSaving}
                      onChange={(e) =>
                        setDraft((d) =>
                          d
                            ? {
                                ...d,
                                stageStatuses: {
                                  ...d.stageStatuses,
                                  [stage.key]: e.target.value as StageStatus,
                                },
                              }
                            : d,
                        )
                      }
                    >
                      {STAGE_STATUSES.map((st) => (
                        <option key={st} value={st}>
                          {st}
                        </option>
                      ))}
                    </select>
                  </div>
                );
              })}
            </div>
          </section>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={isSaving || !dirty}
            onClick={() => onSave(draft, original)}
          >
            {isSaving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </>
  );
}

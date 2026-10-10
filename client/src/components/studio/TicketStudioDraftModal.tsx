import { useEffect, useId, useMemo, useState } from "react";

import { IconCloseButton } from "../IconCloseButton";
import { Input } from "../ui/Input";

import type { StudioWorkflow, TicketStudioDraftItem } from "../../api/client";
import {
  formatAcceptanceCriteriaText,
  parseAcceptanceCriteriaText,
  priorityLabel,
} from "../../lib/importTicketPreview";
import { workItemTypeLabel } from "../../lib/workItemHierarchy";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";
import { Textarea } from "../ui/Textarea";
import { ModalShell } from "../ui/ModalShell";

const TYPE_OPTIONS = ["feature", "capability", "task", "bug", "milestone", "initiative"] as const;
const PRIORITY_OPTIONS = [1, 2, 3] as const;

export interface TicketStudioDraftModalProps {
  item: TicketStudioDraftItem | null;
  allItems: TicketStudioDraftItem[];
  workflowOptions: StudioWorkflow[];
  isOpen: boolean;
  readOnly?: boolean;
  onClose: () => void;
  onSave?: (item: TicketStudioDraftItem) => void;
}

function draftEquals(a: TicketStudioDraftItem, b: TicketStudioDraftItem): boolean {
  return (
    a.ref === b.ref &&
    a.work_item_type === b.work_item_type &&
    a.parent_ref === b.parent_ref &&
    a.title === b.title &&
    a.description === b.description &&
    a.priority === b.priority &&
    (a.workflow_template_slug ?? "") === (b.workflow_template_slug ?? "") &&
    a.selected === b.selected &&
    a.acceptance_criteria.join("\n") === b.acceptance_criteria.join("\n")
  );
}

export function TicketStudioDraftModal({
  item,
  allItems,
  workflowOptions,
  isOpen,
  readOnly = false,
  onClose,
  onSave,
}: TicketStudioDraftModalProps) {
  const [draft, setDraft] = useState<TicketStudioDraftItem | null>(null);
  const [acceptanceText, setAcceptanceText] = useState("");
  const kindLabelId = useId();

  useEffect(() => {
    if (!item) {
      setDraft(null);
      setAcceptanceText("");
      return;
    }
    setDraft({ ...item, acceptance_criteria: [...item.acceptance_criteria] });
    setAcceptanceText(formatAcceptanceCriteriaText(item.acceptance_criteria));
  }, [item?.ref, item?.title, item?.description, item?.work_item_type, item?.parent_ref, item?.priority, item?.workflow_template_slug, item?.selected, item?.acceptance_criteria]);

  const parentOptions = useMemo(
    () => allItems.filter((candidate) => candidate.ref !== item?.ref),
    [allItems, item?.ref],
  );

  const workflowSlugs = useMemo(
    () => new Set(workflowOptions.map((workflow) => workflow.slug)),
    [workflowOptions],
  );

  if (!isOpen || !item || !draft) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="studio-draft-modal-title">{null}</ModalShell>;
  }

  const nextDraft: TicketStudioDraftItem = {
    ...draft,
    acceptance_criteria: parseAcceptanceCriteriaText(acceptanceText),
  };
  const isDirty = !draftEquals(nextDraft, item);
  const canSave = !readOnly && isDirty && nextDraft.title.trim().length > 0 && !!onSave;
  // A slug the workspace no longer offers still shows, so a stale draft never
  // silently loses its choice on open.
  const hasCustomWorkflow = Boolean(
    nextDraft.workflow_template_slug &&
      !workflowSlugs.has(nextDraft.workflow_template_slug),
  );

  const patch = (patch: Partial<TicketStudioDraftItem>) => {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  };

  const handleSave = () => {
    if (!canSave) return;
    onSave({
      ...nextDraft,
      title: nextDraft.title.trim(),
    });
    onClose();
  };

  return (
    <ModalShell open onDismiss={onClose} labelledBy="studio-draft-modal-title" panelClassName="modal-panel-wide">
      <div className="modal-header">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div id={kindLabelId} className="state-label">Draft ticket</div>
          <Input
            id="studio-draft-modal-title"
            aria-labelledby={kindLabelId}
            className="btn-secondary filter-select modal-title"
            style={{ width: "100%", fontSize: 16, fontWeight: 600, marginTop: 4 }}
            value={draft.title}
            readOnly={readOnly}
            placeholder="Ticket title"
            onChange={(e) => patch({ title: e.target.value })}
          />
          <p className="modal-subtitle" style={{ fontFamily: "var(--mono)" }}>
            {draft.ref}
          </p>
        </div>
        <IconCloseButton className="modal-close-btn" onClick={onClose} />
      </div>

      <div className="modal-body">
        <div className="modal-field">
          <label className="modal-field-label" htmlFor="studio-draft-type">
            Type
          </label>
          <Select
            id="studio-draft-type"
            className="btn-secondary filter-select"
            style={{ width: "100%" }}
            value={draft.work_item_type}
            disabled={readOnly}
            onChange={(e) =>
              patch({ work_item_type: e.target.value as TicketStudioDraftItem["work_item_type"] })
            }
          >
            {TYPE_OPTIONS.map((type) => (
              <option key={type} value={type}>
                {workItemTypeLabel(type)}
              </option>
            ))}
          </Select>
        </div>

        <div className="modal-field">
          <label className="modal-field-label" htmlFor="studio-draft-parent">
            Parent (draft ref)
          </label>
          <Select
            id="studio-draft-parent"
            className="btn-secondary filter-select"
            style={{ width: "100%" }}
            value={draft.parent_ref ?? ""}
            disabled={readOnly}
            onChange={(e) => patch({ parent_ref: e.target.value || null })}
          >
            <option value="">None (root)</option>
            {parentOptions.map((parent) => (
              <option key={parent.ref} value={parent.ref}>
                {parent.ref} · {parent.title}
              </option>
            ))}
          </Select>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div className="modal-field">
            <label className="modal-field-label" htmlFor="studio-draft-priority">
              Priority
            </label>
            <Select
              id="studio-draft-priority"
              className="btn-secondary filter-select"
              style={{ width: "100%" }}
              value={draft.priority}
              disabled={readOnly}
              onChange={(e) => patch({ priority: Number(e.target.value) })}
            >
              {PRIORITY_OPTIONS.map((priority) => (
                <option key={priority} value={priority}>
                  {priorityLabel(priority)}
                </option>
              ))}
            </Select>
          </div>

          <div className="modal-field">
            <label className="modal-field-label" htmlFor="studio-draft-workflow">
              Workflow
            </label>
            <Select
              id="studio-draft-workflow"
              className="btn-secondary filter-select"
              style={{ width: "100%" }}
              value={draft.workflow_template_slug ?? ""}
              disabled={readOnly}
              onChange={(e) => patch({ workflow_template_slug: e.target.value })}
            >
              <option value="">Workspace default</option>
              {workflowOptions.map((workflow) => (
                <option key={workflow.slug} value={workflow.slug}>
                  {workflow.name}
                </option>
              ))}
              {hasCustomWorkflow && (
                <option value={nextDraft.workflow_template_slug}>
                  {nextDraft.workflow_template_slug} (not found)
                </option>
              )}
            </Select>
          </div>
        </div>

        <div className="modal-field">
          <label className="modal-field-label" htmlFor="studio-draft-description">
            Description
          </label>
          <Textarea
            id="studio-draft-description"
            className="btn-secondary"
            style={{ width: "100%", minHeight: 120, boxSizing: "border-box", fontSize: 12.5 }}
            value={draft.description}
            readOnly={readOnly}
            placeholder="Problem, approach, constraints…"
            onChange={(e) => patch({ description: e.target.value })}
          />
        </div>

        <div className="modal-field">
          <label className="modal-field-label" htmlFor="studio-draft-ac">
            Acceptance criteria
          </label>
          <p className="modal-hint" style={{ margin: "0 0 6px" }}>
            One criterion per line. Prefix with - or * if you like.
          </p>
          <Textarea
            id="studio-draft-ac"
            className="btn-secondary"
            style={{ width: "100%", minHeight: 120, boxSizing: "border-box", fontSize: 12 }}
            value={acceptanceText}
            readOnly={readOnly}
            placeholder="- User can …&#10;- API returns …"
            onChange={(e) => setAcceptanceText(e.target.value)}
          />
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--txm)" }}>
          <Input
            type="checkbox"
            checked={draft.selected}
            disabled={readOnly}
            onChange={(e) => patch({ selected: e.target.checked })}
          />
          Include when creating tickets in workspace
        </label>
      </div>

      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          {readOnly ? "Close" : "Cancel"}
        </Button>
        {!readOnly && onSave && (
          <Button variant="primary" disabled={!canSave} onClick={handleSave}>
            Save to draft
          </Button>
        )}
      </div>
    </ModalShell>
  );
}

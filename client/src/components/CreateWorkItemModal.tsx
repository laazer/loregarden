import { useEffect, useMemo, useState } from "react";

import type { TicketSummary, TicketTreeNode, WorkItemType } from "../api/client";
import {
  allowedChildTypes,
  defaultChildType,
  workItemTypeLabel,
} from "../lib/workItemHierarchy";

const WORK_ITEM_TYPES: { id: WorkItemType; label: string }[] = [
  { id: "milestone", label: "Milestone" },
  { id: "feature", label: "Feature" },
  { id: "capability", label: "Capability" },
  { id: "task", label: "Task" },
  { id: "bug", label: "Bug" },
];

const REQUIRED_PARENT: Partial<Record<WorkItemType, WorkItemType>> = {
  feature: "milestone",
  capability: "feature",
  task: "capability",
  bug: "capability",
};

export interface CreateWorkItemDraft {
  title: string;
  work_item_type: WorkItemType;
  parent_ticket_id: string;
  description: string;
  acceptance_criteria: string;
  priority: number;
}

interface CreateWorkItemModalProps {
  open: boolean;
  workspaceSlug: string;
  workspacePicker: boolean;
  workspaces: { slug: string; name: string }[];
  onWorkspaceSlugChange: (slug: string) => void;
  tickets: TicketSummary[];
  selectedTicketId: string | null;
  ticketTree: TicketTreeNode[];
  parentTicketId?: string | null;
  parentTicketTitle?: string;
  parentTicketType?: WorkItemType | null;
  lockParent?: boolean;
  isSaving: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onCreate: (draft: CreateWorkItemDraft) => Promise<void>;
}

function findNode(nodes: TicketTreeNode[], id: string): TicketTreeNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const child = findNode(node.children, id);
    if (child) return child;
  }
  return null;
}

function initialDraft(
  tickets: TicketSummary[],
  selectedTicketId: string | null,
  ticketTree: TicketTreeNode[],
  lockedParent?: { id: string; type: WorkItemType } | null,
): CreateWorkItemDraft {
  if (lockedParent) {
    return {
      title: "",
      work_item_type: defaultChildType(lockedParent.type),
      parent_ticket_id: lockedParent.id,
      description: "",
      acceptance_criteria: "",
      priority: 3,
    };
  }

  const selected = selectedTicketId ? tickets.find((t) => t.id === selectedTicketId) : undefined;
  const selectedNode = selectedTicketId ? findNode(ticketTree, selectedTicketId) : null;

  let work_item_type: WorkItemType = "task";
  let parent_ticket_id = "";

  if (selected) {
    work_item_type = defaultChildType(selected.work_item_type);
    if (selected.work_item_type === "task" || selected.work_item_type === "bug") {
      parent_ticket_id = selected.parent_ticket_id ?? "";
    } else {
      parent_ticket_id = selected.id;
    }
  } else if (selectedNode) {
    work_item_type = defaultChildType(selectedNode.work_item_type);
    parent_ticket_id = selectedNode.id;
  } else {
    work_item_type = "milestone";
  }

  return {
    title: "",
    work_item_type,
    parent_ticket_id,
    description: "",
    acceptance_criteria: "",
    priority: 3,
  };
}

export function CreateWorkItemModal({
  open,
  workspaceSlug,
  workspacePicker,
  workspaces,
  onWorkspaceSlugChange,
  tickets,
  selectedTicketId,
  ticketTree,
  parentTicketId = null,
  parentTicketTitle = "",
  parentTicketType = null,
  lockParent = false,
  isSaving,
  errorMessage,
  onClose,
  onCreate,
}: CreateWorkItemModalProps) {
  const lockedParent =
    lockParent && parentTicketId && parentTicketType
      ? { id: parentTicketId, type: parentTicketType }
      : null;

  const [draft, setDraft] = useState<CreateWorkItemDraft>(() =>
    initialDraft(tickets, selectedTicketId, ticketTree, lockedParent),
  );

  useEffect(() => {
    if (!open) return;
    setDraft(initialDraft(tickets, selectedTicketId, ticketTree, lockedParent));
  }, [open, selectedTicketId, tickets, ticketTree, lockedParent?.id, lockedParent?.type]);

  const typeOptions = useMemo(() => {
    if (lockedParent) {
      return allowedChildTypes(lockedParent.type).map((id) => ({
        id,
        label: workItemTypeLabel(id),
      }));
    }
    return WORK_ITEM_TYPES;
  }, [lockedParent]);

  const parentOptions = useMemo(() => {
    const required = REQUIRED_PARENT[draft.work_item_type];
    if (!required) return [];
    return tickets.filter((t) => t.work_item_type === required);
  }, [draft.work_item_type, tickets]);

  useEffect(() => {
    if (lockParent) return;
    if (draft.work_item_type === "milestone") {
      if (draft.parent_ticket_id) {
        setDraft((d) => ({ ...d, parent_ticket_id: "" }));
      }
      return;
    }
    if (parentOptions.length === 0) {
      if (draft.parent_ticket_id) {
        setDraft((d) => ({ ...d, parent_ticket_id: "" }));
      }
      return;
    }
    if (!parentOptions.some((p) => p.id === draft.parent_ticket_id)) {
      setDraft((d) => ({ ...d, parent_ticket_id: parentOptions[0].id }));
    }
  }, [draft.work_item_type, draft.parent_ticket_id, parentOptions, lockParent]);

  useEffect(() => {
    if (!lockedParent) return;
    const allowed = allowedChildTypes(lockedParent.type);
    if (!allowed.includes(draft.work_item_type)) {
      setDraft((d) => ({ ...d, work_item_type: defaultChildType(lockedParent.type) }));
    }
  }, [draft.work_item_type, lockedParent]);

  if (!open) return null;

  const needsParent = draft.work_item_type !== "milestone";
  const canSubmit =
    draft.title.trim().length > 0 &&
    !!workspaceSlug &&
    (!needsParent || !!draft.parent_ticket_id);

  const handleCreate = () => {
    if (!canSubmit) return;
    void onCreate(draft);
  };

  const modalTitle = lockParent ? "Add sub-item" : "New work item";
  const modalSubtitle = lockParent
    ? `Under ${parentTicketTitle || parentTicketId}`
    : "Add a milestone, container, task, or bug to the tree";

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="create-work-item-title">
        <div className="modal-header">
          <div>
            <div className="state-label">{workspaceSlug}</div>
            <h2 id="create-work-item-title" className="modal-title">
              {modalTitle}
            </h2>
            <p className="modal-subtitle">{modalSubtitle}</p>
          </div>
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          {workspacePicker && !lockParent && (
            <div className="modal-field">
              <div className="modal-field-label">Workspace</div>
              <select
                className="btn-secondary filter-select"
                style={{ width: "100%", fontSize: 12 }}
                value={workspaceSlug}
                disabled={isSaving || workspaces.length === 0}
                onChange={(e) => onWorkspaceSlugChange(e.target.value)}
              >
                {workspaces.length === 0 ? (
                  <option value="">No workspaces available</option>
                ) : (
                  workspaces.map((w) => (
                    <option key={w.slug} value={w.slug}>
                      {w.name}
                    </option>
                  ))
                )}
              </select>
            </div>
          )}

          {!workspaceSlug && (
            <p className="modal-hint">Select a workspace before creating work items.</p>
          )}

          {errorMessage && (
            <p className="modal-hint" style={{ color: "var(--rdl)" }}>
              {errorMessage}
            </p>
          )}

          <div className="modal-field">
            <div className="modal-field-label">Type</div>
            <select
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12 }}
              value={draft.work_item_type}
              disabled={isSaving || typeOptions.length === 0}
              onChange={(e) =>
                setDraft((d) => ({ ...d, work_item_type: e.target.value as WorkItemType }))
              }
            >
              {typeOptions.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          {needsParent && !lockParent && (
            <div className="modal-field">
              <div className="modal-field-label">Parent</div>
              <select
                className="btn-secondary filter-select"
                style={{ width: "100%", fontSize: 12 }}
                value={draft.parent_ticket_id}
                disabled={isSaving || parentOptions.length === 0}
                onChange={(e) => setDraft((d) => ({ ...d, parent_ticket_id: e.target.value }))}
              >
                {parentOptions.length === 0 ? (
                  <option value="">
                    No valid parent — create a {REQUIRED_PARENT[draft.work_item_type]} first
                  </option>
                ) : (
                  parentOptions.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.external_id} · {p.title}
                    </option>
                  ))
                )}
              </select>
            </div>
          )}

          {needsParent && lockParent && (
            <div className="modal-field">
              <div className="modal-field-label">Parent</div>
              <div
                className="btn-secondary filter-select"
                style={{ width: "100%", fontSize: 12, boxSizing: "border-box", opacity: 0.85 }}
              >
                {parentTicketTitle || parentTicketId}
              </div>
            </div>
          )}

          <div className="modal-field">
            <div className="modal-field-label">Title</div>
            <input
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12 }}
              value={draft.title}
              disabled={isSaving}
              placeholder="What needs to be done?"
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            />
          </div>

          <div className="modal-field">
            <div className="modal-field-label">Description</div>
            <textarea
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12, minHeight: 72, resize: "vertical" }}
              value={draft.description}
              disabled={isSaving}
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
            />
          </div>

          {(
            <div className="modal-field">
              <div className="modal-field-label">Acceptance criteria (one per line)</div>
              <textarea
                className="btn-secondary filter-select"
                style={{ width: "100%", fontSize: 12, minHeight: 72, resize: "vertical" }}
                value={draft.acceptance_criteria}
                disabled={isSaving}
                placeholder="- Criterion one&#10;- Criterion two"
                onChange={(e) => setDraft((d) => ({ ...d, acceptance_criteria: e.target.value }))}
              />
            </div>
          )}

          <div className="modal-field">
            <div className="modal-field-label">Priority</div>
            <select
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12 }}
              value={draft.priority}
              disabled={isSaving}
              onChange={(e) => setDraft((d) => ({ ...d, priority: Number(e.target.value) }))}
            >
              <option value={1}>P1 — High</option>
              <option value={2}>P2 — Medium</option>
              <option value={3}>P3 — Low</option>
            </select>
          </div>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={isSaving || !canSubmit} onClick={handleCreate}>
            {isSaving ? "Creating…" : lockParent ? "Add sub-item" : "Create work item"}
          </button>
        </div>
      </div>
    </>
  );
}

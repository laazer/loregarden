import { useState } from "react";

import type { InitiativeMilestone } from "../../api/client";

interface CreateInitiativeFormProps {
  /** Milestones ticked on the page; they are attached as soon as the initiative exists. */
  milestones: InitiativeMilestone[];
  isSaving: boolean;
  onCreate: (draft: { title: string; description: string }) => Promise<unknown>;
  onCancel: () => void;
}

/** Inline, not a modal: creating one is two fields, and the list it lands in stays visible. */
export function CreateInitiativeForm({ milestones, isSaving, onCreate, onCancel }: CreateInitiativeFormProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const canSubmit = title.trim().length > 0 && !isSaving;

  return (
    <form
      className="initiative-create"
      aria-label="New initiative"
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit) return;
        void onCreate({ title: title.trim(), description: description.trim() }).then(() => {
          setTitle("");
          setDescription("");
        });
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !isSaving) onCancel();
      }}
    >
      <label className="modal-field">
        <span className="modal-field-label">Title</span>
        <input
          className="btn-secondary filter-select initiative-input"
          value={title}
          autoFocus
          disabled={isSaving}
          placeholder="Name the shared goal its milestones work toward"
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="modal-field">
        <span className="modal-field-label">Description</span>
        <textarea
          className="btn-secondary filter-select initiative-input"
          rows={3}
          value={description}
          disabled={isSaving}
          placeholder="What does finishing this initiative mean?"
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      {milestones.length > 0 ? (
        <div className="initiative-create-picked">
          <span className="modal-field-label">
            Attaches {milestones.length} {milestones.length === 1 ? "milestone" : "milestones"}
          </span>
          <ul>
            {milestones.map((m) => (
              <li key={m.id}>
                <span className="initiative-ws-pill">{m.workspace_slug}</span> {m.title}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="modal-hint">
          Tick milestones below to attach them on creation, or attach them to the initiative afterwards.
        </p>
      )}
      <div className="initiative-create-actions">
        <button type="button" className="btn-secondary" disabled={isSaving} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={!canSubmit}>
          {isSaving ? "Creating…" : "Create initiative"}
        </button>
      </div>
    </form>
  );
}

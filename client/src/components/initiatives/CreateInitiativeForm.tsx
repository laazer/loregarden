import { useState } from "react";

interface CreateInitiativeFormProps {
  isSaving: boolean;
  onCreate: (draft: { title: string; description: string }) => Promise<unknown>;
  onCancel: () => void;
}

/** Inline, not a modal: creating one is two fields, and the list it lands in stays visible. */
export function CreateInitiativeForm({ isSaving, onCreate, onCancel }: CreateInitiativeFormProps) {
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
          placeholder="e.g. Cross-repo auth migration"
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
      <p className="modal-hint">
        Initiatives span workspaces. Attach milestones from any workspace once it exists.
      </p>
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

/**
 * The New View form: a kind, a name, and nothing else.
 *
 * The kind picked here chooses which *layout* to seed, not a field to submit —
 * `ViewCreate` is `extra="forbid"` and the kind is the layout's discriminator,
 * so a body carrying it twice is a 422. That translation happens in
 * `useSidebarTabs`; this component only collects the choice.
 *
 * It stays open while the create is in flight and reports a refusal in place:
 * the failure a malformed create produces is a modal that will not close, and
 * the only thing worse is one that closes on a view that was never made.
 */

import { useId, useState, type FormEvent } from "react";

import type { ViewKind } from "../lib/viewsApi";
import { IconCloseButton } from "./IconCloseButton";

const KIND_OPTIONS: { kind: ViewKind; label: string; hint: string }[] = [
  { kind: "flex_grid", label: "Flex grid", hint: "Nested splits that fill the screen." },
  { kind: "canvas", label: "Canvas", hint: "Free-placed panes on a pannable surface." },
];

/** Mounted only while open, so each visit starts on an empty form. */
export function NewViewModal({
  isCreating,
  error,
  onClose,
  onCreate,
}: {
  isCreating: boolean;
  error: string;
  onClose: () => void;
  onCreate: (input: { title: string; kind: ViewKind }) => void;
}) {
  const titleId = useId();
  const groupName = useId();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ViewKind>("flex_grid");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (isCreating) return;
    onCreate({ title: title.trim() || "Untitled view", kind });
  };

  return (
    <>
      <div className="modal-overlay" onClick={isCreating ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby={titleId} aria-modal="true">
        <form onSubmit={submit}>
          <div className="modal-header">
            <div>
              <div className="state-label">Tabs</div>
              <h2 id={titleId} className="modal-title">
                New view
              </h2>
            </div>
            <IconCloseButton disabled={isCreating} onClick={onClose} />
          </div>

          <div className="modal-body">
            <label className="field-label" htmlFor={`${titleId}-name`}>
              Name
            </label>
            <input
              id={`${titleId}-name`}
              className="input"
              value={title}
              autoFocus
              placeholder="Untitled view"
              onChange={(event) => setTitle(event.target.value)}
            />

            <div style={{ marginTop: 14, display: "grid", gap: 8 }} role="radiogroup">
              {KIND_OPTIONS.map((option) => (
                <label
                  key={option.kind}
                  style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}
                >
                  <input
                    type="radio"
                    name={groupName}
                    checked={kind === option.kind}
                    onChange={() => setKind(option.kind)}
                  />
                  <span>
                    <span style={{ display: "block", fontSize: 13 }}>{option.label}</span>
                    <span style={{ display: "block", fontSize: 12, color: "var(--txm)" }}>
                      {option.hint}
                    </span>
                  </span>
                </label>
              ))}
            </div>

            {error ? (
              <div
                style={{
                  marginTop: 12,
                  padding: "10px 12px",
                  borderRadius: 10,
                  border: "1px solid rgba(255, 106, 84, 0.35)",
                  background: "rgba(255, 106, 84, 0.08)",
                  color: "var(--rdl)",
                  fontSize: 12,
                  lineHeight: 1.45,
                }}
              >
                {error}
              </div>
            ) : null}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn-secondary" disabled={isCreating} onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={isCreating}>
              {isCreating ? "Creating…" : "Create view"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

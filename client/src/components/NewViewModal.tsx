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
 *
 * The markup here always asked for `.field-label` and `.input`; until 556 neither
 * class existed anywhere in the client, and with no element-level `input` rule to
 * fall back on the browser default painted a white box on a #0b0f16 dialog (555).
 * The fix was to give those names a definition beside the six feature-scoped copies
 * that had grown up instead of one — not to invent a seventh style for this dialog.
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

            {/*
              Real radio inputs inside label cards: the radiogroup semantics and
              the arrow-key behaviour are the browser's, and only the painting is
              ours. The native dot stays visible rather than being hidden behind
              an accent ring — the dot is what says "one of these", and a ring
              alone has to be learned.
            */}
            <div className="view-kind-group" role="radiogroup" aria-label="View kind">
              {KIND_OPTIONS.map((option) => (
                <label
                  key={option.kind}
                  className={`view-kind-option${kind === option.kind ? " is-selected" : ""}`}
                >
                  <input
                    type="radio"
                    name={groupName}
                    checked={kind === option.kind}
                    onChange={() => setKind(option.kind)}
                  />
                  <span>
                    <span className="view-kind-label">{option.label}</span>
                    <span className="view-kind-hint">{option.hint}</span>
                  </span>
                </label>
              ))}
            </div>

            {error ? <div className="form-error">{error}</div> : null}
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

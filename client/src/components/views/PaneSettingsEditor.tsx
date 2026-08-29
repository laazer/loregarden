/**
 * The form a pane's settings are edited in — generated from the primitive's own
 * `settingsFields`, never written per primitive.
 *
 * `SettingsField` is a discriminated union on `kind` precisely so this file can
 * switch on it and get a correctly typed input and a correctly typed `default`
 * without narrowing either by hand. Adding a primitive is an entry in the
 * registry and no change here — the same rule the picker already follows, and
 * the reason this component names no primitive id, no field key and no label.
 *
 * ## Where the values go
 *
 * Through `useContainerSettingsWrite`, which is `useViewLayoutEdit` with a
 * container composed by the registry. That matters twice over: the write carries
 * its target view in the mutation's variables rather than reading it from this
 * render's closure, and it queues behind every other write to the same view. A
 * settings PATCH racing a split would otherwise revert it, because PATCH
 * replaces the layout whole.
 *
 * The container is *replaced*, not merged into. See `containerWithSettings`.
 *
 * ## What is validated here, and what deliberately is not
 *
 * Only that a value is of the *kind* its field declares. Whether a workspace
 * slug exists or a ticket id resolves is not this form's question — the server
 * does not validate `settings` either, and a value naming something absent is
 * supposed to render the primitive's own empty state rather than block the edit.
 *
 * The one exception is a number that does not parse, and it is not an exception
 * to that rule. `NaN` serialises to `null`, which 444 established the server
 * accepts silently; sending it would discard the field with no feedback at all.
 * So an unparseable number is refused, in the field, where it can be fixed.
 *
 * Both rules live in `paneSettingsDraft`, which is the pure half of this
 * component: what a draft is seeded with, and what it settles as.
 */

import { useId, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { useContainerSettingsWrite } from "../../hooks/useViewLayoutEdit";
import { asJson } from "../../lib/viewLayouts";
import { useSidebarWorkspaceSlug } from "../../state/SidebarWorkspaceContext";
import "./paneChrome.css";
import { initialDraft, readDraft, type DraftValue } from "./paneSettingsDraft";
import type { RegisteredPrimitive } from "./primitives/types";

export function PaneSettingsEditor({
  containerId,
  container,
  primitive,
  onDone,
}: {
  containerId: string;
  /** The container as the layout stores it: unvalidated, and possibly absent. */
  container: unknown;
  /** The primitive whose schema this form is generated from. */
  primitive: RegisteredPrimitive;
  /** Called once the form is finished with, saved or abandoned. */
  onDone: () => void;
}) {
  const slug = useSidebarWorkspaceSlug();
  // Outside the view route there is no id, and the write refuses to compose a
  // PATCH without one — a pane can only edit the view underneath it.
  const { viewId = "" } = useParams<{ viewId: string }>();
  const writeSettings = useContainerSettingsWrite(slug, viewId);
  const fieldId = useId();

  const stored = asJson(asJson(container)?.settings) ?? {};
  // Seeded once. Re-deriving from the record on every render would discard the
  // half-typed value the moment any other write to this view landed.
  const [draft, setDraft] = useState(() => initialDraft(primitive.settingsFields, stored));
  const [errors, setErrors] = useState<ReadonlyMap<string, string>>(new Map());

  function setValue(key: string, value: DraftValue) {
    setDraft((previous) => new Map(previous).set(key, value));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const { values, errors: refused } = readDraft(primitive.settingsFields, draft);
    setErrors(refused);
    if (refused.size > 0) return;
    writeSettings(containerId, primitive.id, values);
    onDone();
  }

  return (
    <form className="pane-settings" onSubmit={submit} aria-label={`${primitive.displayName} settings`}>
      {primitive.settingsFields.map((field) => {
        const inputId = `${fieldId}-${field.key}`;
        const helpId = `${inputId}-help`;
        const errorId = `${inputId}-error`;
        const error = errors.get(field.key);
        const value = draft.get(field.key);
        // The hint and the refusal both name this input, so a screen reader
        // hears why the field was rejected rather than only that it was.
        const describedBy =
          [field.help === undefined ? "" : helpId, error === undefined ? "" : errorId]
            .filter((id) => id !== "")
            .join(" ") || undefined;

        if (field.kind === "boolean") {
          return (
            <div className="pane-settings-field" key={field.key}>
              <div className="pane-settings-check">
                <input
                  id={inputId}
                  type="checkbox"
                  checked={value === true}
                  aria-describedby={describedBy}
                  onChange={(event) => setValue(field.key, event.target.checked)}
                />
                {/* The label is the control's accessible name, not decoration
                    beside it — a checkbox named only by adjacent text has no
                    name at all, and no hit area beyond 13 pixels. */}
                <label htmlFor={inputId}>{field.label}</label>
              </div>
              {field.help === undefined ? null : (
                <p className="pane-settings-help" id={helpId}>
                  {field.help}
                </p>
              )}
            </div>
          );
        }

        return (
          <div className="pane-settings-field" key={field.key}>
            <label className="field-label" htmlFor={inputId}>
              {field.label}
            </label>
            <input
              id={inputId}
              className="input"
              // The union's whole purpose: `kind` picks the input, so a number
              // field is never a free-text box this form then has to police.
              type={field.kind === "number" ? "number" : "text"}
              value={typeof value === "string" ? value : ""}
              aria-describedby={describedBy}
              aria-invalid={error === undefined ? undefined : true}
              onChange={(event) => setValue(field.key, event.target.value)}
            />
            {field.help === undefined ? null : (
              <p className="pane-settings-help" id={helpId}>
                {field.help}
              </p>
            )}
            {error === undefined ? null : (
              <p className="pane-settings-error" id={errorId} role="alert">
                {error}
              </p>
            )}
          </div>
        );
      })}
      <div className="pane-settings-actions">
        <button type="button" className="btn-secondary btn-compact" onClick={onDone}>
          Cancel
        </button>
        <button type="submit" className="btn-primary btn-compact">
          Save
        </button>
      </div>
    </form>
  );
}

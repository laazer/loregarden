/**
 * The input a `choice` settings field renders: the app's own list, not a box.
 *
 * Separate from `PaneSettingsEditor` because it needs a hook — the options are
 * fetched — and hooks cannot be called inside the editor's `map` over fields.
 * The editor keeps the form; this keeps one field.
 *
 * ## Three states, and a text box under all of them
 *
 * Options are a convenience over a value that was always free text, never a
 * gate on it. So every path here ends at something an operator can type into or
 * pick from, and none of them can refuse a value:
 *
 * - **loading** — the text box, with the field's hint replaced by a line saying
 *   the list is coming. Disabling it would take the field away from anyone who
 *   already knows what they want to type.
 * - **unavailable** — the fetch failed, or returned nothing, or the workspace
 *   the list is scoped to has not resolved. The text box again, unchanged from
 *   what the field was before this file existed. A settings form that breaks
 *   because a list endpoint is down is worse than one with no list.
 * - **available** — a `select` for a short closed set, or a `datalist`-backed
 *   text input for tickets, which run to hundreds.
 *
 * ## A stored value the list does not contain is kept
 *
 * A view can name a workspace that has since been renamed, or a ticket from
 * another workspace. `select` cannot show a value it has no `option` for — it
 * silently displays the first one instead, and the next Save would write *that*
 * over the operator's value without anyone touching the field. So an unlisted
 * value gets an option of its own, marked as unlisted. The `suggest` path needs
 * none of this: it is a text input, and a datalist only suggests.
 */

import { useId } from "react";

import { useChoiceOptions } from "../../hooks/usePaneSettingsChoices";
import { useSidebarWorkspaceSlug } from "../../state/SidebarWorkspaceContext";
import type { SettingsField } from "./primitives/types";

type ChoiceField = Extract<SettingsField, { kind: "choice" }>;

export interface PaneSettingsChoiceInputProps {
  field: ChoiceField;
  inputId: string;
  value: string;
  describedBy: string | undefined;
  invalid: boolean;
  onChange: (value: string) => void;
  /** Rendered under the input: the field's help, or what replaced it. */
  renderHelp: (text: string | undefined) => React.ReactNode;
}

export function PaneSettingsChoiceInput({
  field,
  inputId,
  value,
  describedBy,
  invalid,
  onChange,
  renderHelp,
}: PaneSettingsChoiceInputProps) {
  const workspaceSlug = useSidebarWorkspaceSlug();
  const choices = useChoiceOptions(field.source, workspaceSlug);
  const listId = useId();

  if (choices.isLoading || choices.isUnavailable) {
    return (
      <>
        <input
          id={inputId}
          className="input"
          type="text"
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
        />
        {renderHelp(choices.isLoading ? "Loading the list…" : field.help)}
      </>
    );
  }

  if (choices.mode === "suggest") {
    return (
      <>
        <input
          id={inputId}
          className="input"
          type="text"
          list={listId}
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
        />
        <datalist id={listId}>
          {choices.options.map((option) => (
            <option key={option.value} value={option.value} label={option.label} />
          ))}
        </datalist>
        {renderHelp(field.help)}
      </>
    );
  }

  const isUnlisted = value !== "" && !choices.options.some((option) => option.value === value);

  return (
    <>
      <select
        id={inputId}
        className="input"
        value={value}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        onChange={(event) => onChange(event.target.value)}
      >
        {/* Clearing the field has to stay possible: an empty value is how a
            primitive is told to fall back, and several show their own empty
            state on it rather than an error. */}
        <option value="">— none —</option>
        {isUnlisted ? <option value={value}>{`${value} (not in this list)`}</option> : null}
        {choices.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {renderHelp(field.help)}
    </>
  );
}

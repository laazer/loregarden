import type { SuggestedItem } from "../../../api/client";
import { Input } from "../../ui/Input";
import { Select } from "../../ui/Select";
import { Textarea } from "../../ui/Textarea";
import { OpenItemButton, SuggestedItemText } from "./SuggestedItemParts";
import type { ThemeEdit } from "./suggestionEdit";

/** The value the "move to" select uses for "not grouped". */
export const UNGROUPED = "";

interface ThemeCardProps {
  theme: ThemeEdit;
  members: SuggestedItem[];
  /** Every theme, for moving a milestone to another. */
  themes: ThemeEdit[];
  disabled: boolean;
  onChange: (next: ThemeEdit) => void;
  onMove: (milestoneId: string, key: string | null) => void;
}

/** One theme: tick it to create it, rename it, move its milestones elsewhere. */
export function ThemeCard({ theme, members, themes, disabled, onChange, onMove }: ThemeCardProps) {
  const headingId = `suggest-${theme.key}-heading`;
  return (
    <article
      className={`initiative-card initiative-suggestion${theme.keep ? "" : " initiative-suggestion-off"}`}
      aria-labelledby={headingId}
    >
      <header className="initiative-card-head">
        <label className="initiative-toggle">
          <Input
            type="checkbox"
            checked={theme.keep}
            disabled={disabled || members.length === 0}
            onChange={(e) => onChange({ ...theme, keep: e.target.checked })}
          />
          <span id={headingId}>Create “{theme.title || "untitled"}”</span>
        </label>
        <span className="initiative-muted">
          {members.length} {members.length === 1 ? "milestone" : "milestones"}
        </span>
      </header>

      <label className="modal-field">
        <span className="modal-field-label">Title</span>
        <Input
          className="btn-secondary filter-select initiative-input"
          value={theme.title}
          disabled={disabled}
          onChange={(e) => onChange({ ...theme, title: e.target.value })}
        />
      </label>
      <label className="modal-field">
        <span className="modal-field-label">Description</span>
        <Textarea
          className="btn-secondary filter-select initiative-input"
          rows={2}
          value={theme.description}
          disabled={disabled}
          placeholder="What does finishing this initiative mean?"
          onChange={(e) => onChange({ ...theme, description: e.target.value })}
        />
      </label>
      {theme.rationale && <p className="initiative-hint">{theme.rationale}</p>}

      {members.length === 0 ? (
        <p className="initiative-hint">Every milestone has been moved out. Move one back to create this initiative.</p>
      ) : (
        <ul className="initiative-pick-list" aria-label={`Milestones in ${theme.title || "this theme"}`}>
          {members.map((m) => (
            <li key={m.id} className="initiative-pick initiative-suggest-row-move">
              <span className="initiative-pick-label initiative-suggest-item">
                <SuggestedItemText item={m} />
              </span>
              <Select
                className="btn-secondary filter-select initiative-suggest-move"
                aria-label={`Group for ${m.title}`}
                value={theme.key}
                disabled={disabled}
                onChange={(e) => onMove(m.id, e.target.value === UNGROUPED ? null : e.target.value)}
              >
                {themes.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.key === theme.key ? `In ${t.title || "untitled"}` : `Move to ${t.title || "untitled"}`}
                  </option>
                ))}
                <option value={UNGROUPED}>Not grouped</option>
              </Select>
              <OpenItemButton item={m} />
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

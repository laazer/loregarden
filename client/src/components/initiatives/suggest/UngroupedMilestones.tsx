import type { SuggestedItem } from "../../../api/client";
import { Select } from "../../ui/Select";
import { OpenItemButton, SuggestedItemText } from "./SuggestedItemParts";
import type { ThemeEdit } from "./suggestionEdit";

/** The value the "add to" select uses to start a new theme from the milestone. */
const NEW_THEME = "__new__";

interface UngroupedProps {
  milestones: SuggestedItem[];
  themes: ThemeEdit[];
  disabled: boolean;
  onAssign: (milestoneId: string, key: string) => void;
  onStartTheme: (milestone: SuggestedItem) => void;
}

/** Open milestones no theme claimed, each one select away from a theme — or a new one. */
export function UngroupedMilestones({ milestones, themes, disabled, onAssign, onStartTheme }: UngroupedProps) {
  if (milestones.length === 0) return null;
  return (
    <details className="initiative-card">
      <summary className="initiative-section-title">{milestones.length} open milestones not grouped</summary>
      <p className="initiative-hint">
        No theme claimed these. Add one to a theme above, or start a new initiative from it; the rest stay on the
        Initiatives page to attach later.
      </p>
      <ul className="initiative-pick-list" aria-label="Open milestones not grouped">
        {milestones.map((m) => (
          <li key={m.id} className="initiative-pick initiative-suggest-row-move">
            <span className="initiative-pick-label initiative-suggest-item">
              <SuggestedItemText item={m} />
            </span>
            <Select
              className="btn-secondary filter-select initiative-suggest-move"
              aria-label={`Group for ${m.title}`}
              value=""
              disabled={disabled}
              onChange={(e) => {
                if (e.target.value === NEW_THEME) onStartTheme(m);
                else if (e.target.value) onAssign(m.id, e.target.value);
              }}
            >
              <option value="">Not grouped</option>
              {themes.map((t) => (
                <option key={t.key} value={t.key}>
                  Add to {t.title || "untitled"}
                </option>
              ))}
              <option value={NEW_THEME}>Start a new initiative</option>
            </Select>
            <OpenItemButton item={m} />
          </li>
        ))}
      </ul>
    </details>
  );
}

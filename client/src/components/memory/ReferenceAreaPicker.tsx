import type { EntryArea } from "../../api/referenceAreasApi";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";
import { areaChipText, useAreas } from "./useAreas";
import "./ReferenceAreas.css";

/**
 * The project areas one reference is for: a chip per picked area (its reason on
 * hover, a button to remove it) and a select to add another. Areas from every
 * workspace are offered; picking one from another workspace also reads the
 * reference for that workspace, which the server does on add.
 */
export function AreaPicker({
  picked,
  onChange,
  name,
  workspace,
  disabled = false,
}: {
  picked: readonly EntryArea[];
  onChange: (next: EntryArea[]) => void;
  /** What the areas are for, in the control's accessible name ("SigNoz"). */
  name: string;
  workspace?: string;
  disabled?: boolean;
}) {
  const areas = useAreas();
  if (areas.isPending) return <span className="rl-muted">Loading areas…</span>;
  if (areas.isError) {
    return (
      <span className="rl-warn" role="status">
        Areas did not load ({describeError(areas.error, "the request failed")}).
      </span>
    );
  }
  const list = areas.data;
  if (list.length === 0) {
    return <span className="rl-muted">No project areas yet; add them under Project areas.</span>;
  }
  const chosen = new Set(picked.map((pick) => pick.area));
  const offered = list.filter((area) => !chosen.has(area.label));

  return (
    <div className="rl-area-picker">
      {picked.length > 0 ? (
        <ul className="rl-area-chips" aria-label={`Areas for ${name}`}>
          {picked.map((pick) => (
            <li key={pick.area} className="rl-area-chip" title={pick.reason || undefined}>
              <span>{areaChipText(pick.area, list, workspace)}</span>
              <Button
                variant="plain"
                compact
                className="rl-area-chip-remove"
                disabled={disabled}
                aria-label={`Remove ${pick.area} from ${name}`}
                onClick={() => onChange(picked.filter((other) => other.area !== pick.area))}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {offered.length > 0 ? (
        <Select
          className="rl-area-add"
          value=""
          disabled={disabled}
          aria-label={`Add an area for ${name}`}
          onChange={(event) => {
            const label = event.target.value;
            if (label) onChange([...picked, { area: label, reason: "" }]);
          }}
        >
          <option value="">{picked.length ? "Add another area…" : "Add an area…"}</option>
          {offered.map((area) => (
            <option key={area.id} value={area.label} title={area.description}>
              {area.workspace_slug} · {area.name}
            </option>
          ))}
        </Select>
      ) : null}
    </div>
  );
}

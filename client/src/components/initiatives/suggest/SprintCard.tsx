import type { InitiativeSuggestion, InitiativeSuggestionSet } from "../../../api/client";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { Textarea } from "../../ui/Textarea";
import { OpenItemButton, SuggestedItemText } from "./SuggestedItemParts";
import { keptSprintItems, stillEmptied, type SprintEdit } from "./suggestionEdit";

interface SprintCardProps {
  sprint: InitiativeSuggestion;
  sizing: InitiativeSuggestionSet["sprint"];
  edit: SprintEdit;
  disabled: boolean;
  onChange: (next: SprintEdit) => void;
}

function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** What the kept items cost against what the pace can finish, said in one line. */
function SprintLoad({ sprint, sizing, edit }: Pick<SprintCardProps, "sprint" | "sizing" | "edit">) {
  const kept = keptSprintItems(sprint, edit);
  const cost = kept.reduce((n, i) => n + i.cost, 0);
  const room = sizing.capacity === null ? null : sizing.capacity - sizing.committed;
  const over = room !== null && cost > room;
  return (
    <p className={`initiative-hint${over ? " initiative-warn" : ""}`} aria-live="polite">
      {kept.length} of {sprint.items.length} items ticked, {cost} open work items
      {room === null
        ? " — no recent pace to size it against, so treat the size as a guess."
        : ` — about ${Math.max(room, 0)} fit in ${sizing.days} days${
            sizing.committed > 0 ? ` after the ${sizing.committed} already in a sprint` : ""
          }.`}
      {over && " Over by " + (cost - (room ?? 0)) + "; untick backlog work, or keep only work in progress."}
    </p>
  );
}

/** The sprint: time-boxed features and bugs, trimmed in bulk or one at a time. */
export function SprintCard({ sprint, sizing, edit, disabled, onChange }: SprintCardProps) {
  const headingId = "suggest-sprint-heading";
  const emptied = stillEmptied(sprint, edit);
  const fieldsDisabled = disabled || !edit.keep;
  const setDropped = (ids: string[]) => onChange({ ...edit, dropped: new Set(ids) });
  const toggle = (id: string) => {
    const dropped = new Set(edit.dropped);
    if (dropped.has(id)) dropped.delete(id);
    else dropped.add(id);
    onChange({ ...edit, dropped });
  };

  return (
    <article
      className={`initiative-card initiative-suggestion${edit.keep ? "" : " initiative-suggestion-off"}`}
      aria-labelledby={headingId}
    >
      <header className="initiative-card-head">
        <label className="initiative-toggle">
          <Input
            type="checkbox"
            checked={edit.keep}
            disabled={disabled}
            onChange={(e) => onChange({ ...edit, keep: e.target.checked })}
          />
          <span id={headingId}>Create the sprint</span>
        </label>
        {sprint.target_date && <span className="initiative-muted">Ends {formatDay(sprint.target_date)}</span>}
      </header>

      {edit.keep && emptied.length > 0 && (
        <div className="initiative-suggest-alert" role="status">
          <strong>
            {emptied.length === 1 ? "1 milestone will be marked done" : `${emptied.length} milestones will be marked done`}
          </strong>
          <p>
            This takes the last open work out of {emptied.join(", ")}. With nothing left to wait on,{" "}
            {emptied.length === 1 ? "it rolls" : "they roll"} up as done. Untick one of {emptied.length === 1 ? "its" : "their"}{" "}
            items to keep {emptied.length === 1 ? "it" : "them"} open.
          </p>
        </div>
      )}

      <label className="modal-field">
        <span className="modal-field-label">Title</span>
        <Input
          className="btn-secondary filter-select initiative-input"
          value={edit.title}
          disabled={fieldsDisabled}
          onChange={(e) => onChange({ ...edit, title: e.target.value })}
        />
      </label>
      <label className="modal-field">
        <span className="modal-field-label">Description</span>
        <Textarea
          className="btn-secondary filter-select initiative-input"
          rows={2}
          value={edit.description}
          disabled={fieldsDisabled}
          onChange={(e) => onChange({ ...edit, description: e.target.value })}
        />
      </label>
      {sprint.rationale && <p className="initiative-hint">{sprint.rationale}</p>}
      <SprintLoad sprint={sprint} sizing={sizing} edit={edit} />

      <div className="initiative-suggest-bulk" role="group" aria-label="Choose sprint items in bulk">
        <Button variant="secondary" compact disabled={fieldsDisabled} onClick={() => setDropped([])}>
          Tick all
        </Button>
        <Button
          variant="secondary"
          compact
          disabled={fieldsDisabled}
          onClick={() => setDropped(sprint.items.filter((i) => i.state !== "in_progress").map((i) => i.id))}
        >
          Only work in progress
        </Button>
        <Button
          variant="secondary"
          compact
          disabled={fieldsDisabled}
          onClick={() => setDropped(sprint.items.map((i) => i.id))}
        >
          Untick all
        </Button>
      </div>

      {sprint.items.length === 0 ? (
        <p className="initiative-hint">Nothing left in this sprint to attach.</p>
      ) : (
        <ul className="initiative-pick-list" aria-label="Sprint items">
          {sprint.items.map((item) => {
            const inputId = `suggest-item-${item.id}`;
            return (
              <li key={item.id} className="initiative-pick">
                <Input
                  type="checkbox"
                  id={inputId}
                  checked={!edit.dropped.has(item.id)}
                  disabled={fieldsDisabled}
                  onChange={() => toggle(item.id)}
                />
                <label htmlFor={inputId} className="initiative-pick-label initiative-suggest-item">
                  <SuggestedItemText item={item} />
                </label>
                <OpenItemButton item={item} />
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}

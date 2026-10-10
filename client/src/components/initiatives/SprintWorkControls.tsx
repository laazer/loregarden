import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { InitiativeMilestone, InitiativeView } from "../../api/client";
import { useDebounced } from "../../hooks/useDebounced";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";
import { SuggestedItemText } from "./suggest/SuggestedItemParts";

/**
 * A feature or bug in a sprint-style initiative cannot be detached — it needs
 * a parent — so it leaves by moving to a milestone in its own workspace. This
 * is also how work a sprint took out of a milestone goes back.
 */
export function MoveToMilestone({
  item,
  milestones,
  busy,
  onMove,
}: {
  item: InitiativeMilestone;
  /** Every milestone that could take it; filtered here to the item's workspace. */
  milestones: InitiativeMilestone[];
  busy: boolean;
  onMove: (milestoneId: string) => void;
}) {
  // Open ones first, each group by id, so a long workspace list scans in a predictable order.
  const choices = milestones
    .filter((m) => m.workspace_slug === item.workspace_slug)
    .sort(
      (a, b) =>
        Number(a.state === "done" || a.state === "wont_do") - Number(b.state === "done" || b.state === "wont_do") ||
        a.external_id.localeCompare(b.external_id, undefined, { numeric: true }),
    );
  if (choices.length === 0) {
    return <span className="initiative-muted">No milestone in {item.workspace_slug}</span>;
  }
  return (
    <Select
      className="btn-secondary filter-select initiative-suggest-move"
      aria-label={`Move ${item.title} to a milestone`}
      value=""
      disabled={busy}
      onChange={(e) => {
        if (e.target.value) onMove(e.target.value);
      }}
    >
      <option value="">{busy ? "Moving…" : "Move to milestone…"}</option>
      {choices.map((m) => (
        <option key={m.id} value={m.id}>
          {m.external_id} — {m.title}
          {m.state === "done" ? " (done)" : ""}
        </option>
      ))}
    </Select>
  );
}

/** Find an open feature or bug in any workspace and pull it into this sprint. */
export function AddSprintWork({
  initiative,
  disabled,
  onAdd,
}: {
  initiative: InitiativeView;
  disabled: boolean;
  onAdd: (ticketId: string) => void;
}) {
  const [text, setText] = useState("");
  const search = useDebounced(text.trim(), 250);
  const results = useQuery({
    queryKey: ["sprint-work-search", initiative.id, search],
    queryFn: () => api.initiativeAddableWork(initiative.id, search),
    enabled: search.length >= 2,
    // The failure renders under the field, where the operator is typing.
    meta: { suppressErrorToast: true },
  });
  const rows = results.data ?? [];
  const inputId = `sprint-add-${initiative.id}`;

  return (
    <div className="initiative-sprint-add">
      <label htmlFor={inputId} className="modal-field-label">
        Add a feature or bug to this sprint
      </label>
      <Input
        id={inputId}
        type="search"
        className="btn-secondary filter-select initiative-input"
        value={text}
        disabled={disabled}
        placeholder="Search by title or id"
        onChange={(e) => setText(e.target.value)}
      />
      {search.length < 2 ? null : results.isPending ? (
        <p className="initiative-hint" aria-busy="true">
          Searching…
        </p>
      ) : results.isError ? (
        <p className="initiative-hint" role="alert">
          Search failed: {describeError(results.error)}
        </p>
      ) : rows.length === 0 ? (
        <p className="initiative-hint">No open feature or bug matches “{search}”.</p>
      ) : (
        <ul className="initiative-pick-list" aria-label="Matching features and bugs">
          {rows.map((t) => (
            <li key={t.id} className="initiative-pick initiative-suggest-row-move">
              <span className="initiative-pick-label initiative-suggest-item">
                <SuggestedItemText item={t} />
              </span>
              <Button
                variant="secondary"
                compact
                disabled={disabled}
                aria-label={`Add ${t.external_id} to ${initiative.title}`}
                onClick={() => {
                  onAdd(t.id);
                  setText("");
                }}
              >
                Add
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

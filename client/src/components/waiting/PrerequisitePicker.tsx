import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { TicketSummary } from "../../api/types";
import { useDebounced } from "../../hooks/useDebounced";
import { ticketStateLabel } from "../../lib/ticketStates";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";

const SEARCH_DEBOUNCE_MS = 200;
const RESULTS = 8;

/**
 * Pick the ticket this one should wait for, by id or by title.
 *
 * It was a bare text box wanting an exact id; 783 live edges, and a person
 * adding one had to look the id up somewhere else first. This searches as they
 * type and still takes a pasted id or external id as written, which is what the
 * server resolves either way.
 */
export function PrerequisitePicker({
  ticketId,
  exclude,
  pending,
  onPick,
}: {
  ticketId: string;
  /** Ids already linked, which the results leave out. */
  exclude: string[];
  pending: boolean;
  onPick: (reference: string) => void;
}) {
  const listId = useId();
  const [value, setValue] = useState("");
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const query = useDebounced(value.trim(), SEARCH_DEBOUNCE_MS);
  const results = useQuery({
    queryKey: ["prerequisite-search", query],
    queryFn: () => api.tickets({ search: query, limit: RESULTS }),
    enabled: query.length >= 2,
  });
  const skip = new Set([ticketId, ...exclude]);
  const options: TicketSummary[] = (results.data ?? []).filter((t) => !skip.has(t.id));
  const expanded = open && query.length >= 2;

  const pick = (reference: string) => {
    if (!reference.trim() || pending) return;
    onPick(reference.trim());
    setValue("");
    setOpen(false);
    setActive(0);
  };

  return (
    <div className="prereq-picker">
      <div className="prereq-picker-row">
        <Input
          type="text"
          role="combobox"
          aria-label="Wait for ticket"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={expanded && options[active] ? `${listId}-${active}` : undefined}
          className="btn-secondary filter-select prereq-picker-input"
          value={value}
          placeholder="Wait for… (search, or paste an id)"
          onChange={(event) => {
            setValue(event.target.value);
            setOpen(true);
            setActive(0);
          }}
          onBlur={() => setOpen(false)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" && options.length > 0) {
              event.preventDefault();
              setOpen(true);
              setActive((index) => Math.min(index + 1, options.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((index) => Math.max(index - 1, 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              pick(expanded && options[active] ? options[active].id : value);
            } else if (event.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        <Button
          variant="secondary"
          className="btn-compact"
          disabled={!value.trim() || pending}
          onClick={() => pick(value)}
        >
          {pending ? "Adding…" : "Add"}
        </Button>
      </div>
      {expanded ? (
        <ul id={listId} role="listbox" aria-label="Matching tickets" className="prereq-picker-list">
          {results.isFetching && options.length === 0 ? (
            <li className="prereq-picker-note">Searching…</li>
          ) : options.length === 0 ? (
            <li className="prereq-picker-note">
              {(results.data ?? []).length > 0
                ? `Every ticket matching “${query}” is already linked or is this one.`
                : `No ticket matches “${query}”. Press Enter to add it as an id.`}
            </li>
          ) : (
            options.map((option, index) => (
              <li
                key={option.id}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                className={`prereq-picker-option${index === active ? " is-active" : ""}`}
                // Before blur, so the pick lands before the list closes.
                onMouseDown={(event) => {
                  event.preventDefault();
                  pick(option.id);
                }}
              >
                <span className="count-pill">{option.external_id}</span> {option.title}
                <span className="prereq-picker-state"> · {ticketStateLabel(option.state)}</span>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

import { useCallback, useRef, useState } from "react";

import type { BlockedOn, TicketState, WorkItemType } from "../api/client";
import { BLOCKED_ON_FILTERS, TYPE_FILTERS } from "../lib/ticketFilterOptions";
import { useDismissOnOutside } from "../hooks/useDismissOnOutside";
import { useAnchoredPanelPosition } from "../hooks/useAnchoredPanelPosition";
import { STATE_LABELS } from "./UpdateStateModal";
import { Input } from "./ui/Input";
import "./TicketPaneFilters.css";

type FilterDropdownProps<T extends string> = {
  title: string;
  allLabel: string;
  options: { id: T; label: string; count?: number }[];
  selected: T[];
  onToggle: (id: T) => void;
  onClear: () => void;
  /** Pin the menu to the trigger's width; off where triggers size to their label. */
  matchWidth?: boolean;
};

function summarizeSelection<T extends string>(
  selected: T[],
  options: { id: T; label: string }[],
  allLabel: string,
): string {
  if (selected.length === 0) return allLabel;
  if (selected.length === 1) {
    return options.find((option) => option.id === selected[0])?.label ?? allLabel;
  }
  return `${selected.length} selected`;
}

export function FilterDropdown<T extends string>({
  title,
  allLabel,
  options,
  selected,
  onToggle,
  onClear,
  matchWidth = true,
}: FilterDropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelStyle = useAnchoredPanelPosition(open, triggerRef, panelRef, { matchWidth });
  const summary = summarizeSelection(selected, options, allLabel);
  const filtered = selected.length > 0;

  const closePanel = useCallback(() => setOpen(false), []);
  useDismissOnOutside(open, rootRef, closePanel);

  return (
    <div className="ticket-filter-dropdown" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`btn-secondary btn-compact ticket-filter-trigger ${filtered ? "ticket-filter-trigger-active" : ""}`}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="ticket-filter-trigger-title">{title}</span>
        <span className="ticket-filter-trigger-value">{summary}</span>
        <span className="ticket-filter-trigger-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open ? (
        <div
          ref={panelRef}
          className="ticket-filter-menu ticket-filter-menu--anchored"
          style={panelStyle ?? { position: "fixed", visibility: "hidden" }}
          role="listbox"
          aria-label={`${title} filters`}
        >
          <button
            type="button"
            role="option"
            aria-selected={selected.length === 0}
            className={`ticket-filter-option ${selected.length === 0 ? "active" : ""}`}
            onClick={onClear}
          >
            <span className="ticket-filter-check" aria-hidden="true">
              {selected.length === 0 ? "✓" : ""}
            </span>
            <span className="ticket-filter-option-label">{allLabel}</span>
          </button>
          {options.map((option) => {
            const active = selected.includes(option.id);
            return (
              <button
                key={option.id}
                type="button"
                role="option"
                aria-selected={active}
                className={`ticket-filter-option ${active ? "active" : ""}`}
                onClick={() => onToggle(option.id)}
              >
                <span className="ticket-filter-check" aria-hidden="true">
                  {active ? "✓" : ""}
                </span>
                <span className="ticket-filter-option-label">{option.label}</span>
                {option.count !== undefined ? (
                  <span className="ticket-filter-count">{option.count}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

const STATE_OPTIONS: TicketState[] = [
  "backlog",
  "in_progress",
  "blocked",
  "parked",
  "done",
  "wont_do",
];

/**
 * The Work items pane's narrowing controls, beside the tree they narrow: a text
 * filter over title and id (the server keeps each match's ancestors, so a hit
 * stays under its milestone), then Status, Blocked on and Type.
 *
 * The text filter used to sit in the topbar beside the app's search box, two
 * boxes side by side of which one quietly narrowed a panel on the left.
 */
export function TicketPaneFilters({
  search,
  onSearch,
  typeFilters,
  stateFilters,
  stateCounts,
  blockedOnFilters,
  blockedOnCounts,
  onToggleType,
  onToggleState,
  onToggleBlockedOn,
  onClearTypes,
  onClearStates,
  onClearBlockedOn,
}: {
  search: string;
  onSearch: (value: string) => void;
  typeFilters: WorkItemType[];
  stateFilters: TicketState[];
  stateCounts: Record<string, number>;
  blockedOnFilters: BlockedOn[];
  blockedOnCounts: Record<string, number>;
  onToggleType: (type: WorkItemType) => void;
  onToggleState: (state: TicketState) => void;
  onToggleBlockedOn: (answer: BlockedOn) => void;
  onClearTypes: () => void;
  onClearStates: () => void;
  onClearBlockedOn: () => void;
}) {
  const stateOptions = STATE_OPTIONS.map((state) => ({
    id: state,
    label: STATE_LABELS[state],
    count: stateCounts[state],
  }));

  const blockedOnOptions = BLOCKED_ON_FILTERS.map((option) => ({
    ...option,
    count: blockedOnCounts[option.id] ?? 0,
  }));

  return (
    <div className="ticket-filter-stack">
      <Input
        type="search"
        className="ticket-filter-search"
        aria-label="Filter work items by title or id"
        placeholder="Filter by title or id…"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && search) {
            event.preventDefault();
            onSearch("");
          }
        }}
      />
      <div className="ticket-filter-bar">
      <FilterDropdown
        title="Status"
        allLabel="All"
        options={stateOptions}
        selected={stateFilters}
        onToggle={onToggleState}
        onClear={onClearStates}
      />
      <FilterDropdown
        title="Blocked on"
        allLabel="Anything"
        options={blockedOnOptions}
        selected={blockedOnFilters}
        onToggle={onToggleBlockedOn}
        onClear={onClearBlockedOn}
      />
      <FilterDropdown
        title="Type"
        allLabel="All types"
        options={TYPE_FILTERS}
        selected={typeFilters}
        onToggle={onToggleType}
        onClear={onClearTypes}
      />
      </div>
    </div>
  );
}

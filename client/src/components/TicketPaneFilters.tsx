import { useEffect, useRef, useState } from "react";

import type { TicketState, WorkItemType } from "../api/client";
import { STATE_LABELS } from "./UpdateStateModal";
import "./TicketPaneFilters.css";

type FilterDropdownProps<T extends string> = {
  title: string;
  allLabel: string;
  options: { id: T; label: string; count?: number }[];
  selected: T[];
  onToggle: (id: T) => void;
  onClear: () => void;
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

function FilterDropdown<T extends string>({
  title,
  allLabel,
  options,
  selected,
  onToggle,
  onClear,
}: FilterDropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const summary = summarizeSelection(selected, options, allLabel);
  const filtered = selected.length > 0;

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="ticket-filter-dropdown" ref={rootRef}>
      <button
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
        <div className="ticket-filter-menu" role="listbox" aria-label={`${title} filters`}>
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

const TYPE_FILTERS: { id: WorkItemType; label: string }[] = [
  { id: "milestone", label: "Milestones" },
  { id: "feature", label: "Features" },
  { id: "capability", label: "Capabilities" },
  { id: "task", label: "Tasks" },
  { id: "bug", label: "Bugs" },
];

const STATE_OPTIONS: TicketState[] = [
  "backlog",
  "in_progress",
  "blocked",
  "done",
  "wont_do",
];

export function TicketPaneFilters({
  typeFilters,
  stateFilters,
  stateCounts,
  onToggleType,
  onToggleState,
  onClearTypes,
  onClearStates,
}: {
  typeFilters: WorkItemType[];
  stateFilters: TicketState[];
  stateCounts: Record<string, number>;
  onToggleType: (type: WorkItemType) => void;
  onToggleState: (state: TicketState) => void;
  onClearTypes: () => void;
  onClearStates: () => void;
}) {
  const stateOptions = STATE_OPTIONS.map((state) => ({
    id: state,
    label: STATE_LABELS[state],
    count: stateCounts[state],
  }));

  return (
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
        title="Type"
        allLabel="All types"
        options={TYPE_FILTERS}
        selected={typeFilters}
        onToggle={onToggleType}
        onClear={onClearTypes}
      />
    </div>
  );
}

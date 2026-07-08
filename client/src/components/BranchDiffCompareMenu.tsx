import { TopbarDropdown, TopbarDropdownItem } from "./TopbarDropdown";

import type { BranchDiffMode, BranchDiffOption } from "../lib/branchTriageApi";

export function BranchDiffCompareMenu({
  options,
  value,
  disabled,
  onChange,
}: {
  options: BranchDiffOption[];
  value: BranchDiffMode;
  disabled?: boolean;
  onChange: (mode: BranchDiffMode) => void;
}) {
  const active = options.find((option) => option.mode === value) ?? options[0];
  if (!active || options.length <= 1) {
    return active ? (
      <span className="branch-triage-diff-compare-label">{active.label}</span>
    ) : null;
  }

  return (
    <TopbarDropdown
      label={
        <span className="branch-triage-diff-compare-trigger">
          Compare: {active.label}
        </span>
      }
      align="right"
    >
      {options.map((option) => (
        <TopbarDropdownItem
          key={option.mode}
          active={option.mode === value}
          onSelect={() => {
            if (!disabled) onChange(option.mode);
          }}
        >
          {option.label}
        </TopbarDropdownItem>
      ))}
    </TopbarDropdown>
  );
}

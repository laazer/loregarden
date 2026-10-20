import type { BlockedOn, StageStatus, TicketState, WorkItemType } from "../../api/types";
import { TICKET_STATE_LABELS, stageStatusLabel } from "../../lib/ticketStates";
import {
  PRIORITY_OPTIONS,
  STAGE_STATUS_OPTIONS,
  TICKET_STATE_OPTIONS,
  countBy,
  rowsForFacet,
  type WorkItemRow,
  type WorkItemsFilters,
} from "../../lib/workItemsTable";
import { BLOCKED_ON_FILTERS, TYPE_FILTERS } from "../../lib/ticketFilterOptions";
import { FilterDropdown } from "../TicketPaneFilters";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";

function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

/**
 * The filters beside the views. Each menu counts `rows` (the current view
 * and workspace) under every other filter, so a count is what picking that
 * value would leave.
 */
export function WorkItemsFilterBar({
  filters,
  onChange,
  rows,
  workspaces,
  totalCount,
}: {
  filters: WorkItemsFilters;
  onChange: (next: WorkItemsFilters) => void;
  rows: WorkItemRow[];
  workspaces: [string, number][];
  totalCount: number;
}) {
  const stateCounts = countBy(rowsForFacet(rows, filters, "states"), (node) => node.state);
  const typeCounts = countBy(rowsForFacet(rows, filters, "types"), (node) => node.work_item_type);
  const prioCounts = countBy(rowsForFacet(rows, filters, "priorities"), (node) => node.priority);
  const stageCounts = countBy(rowsForFacet(rows, filters, "stageStatuses"), (node) =>
    node.workflow_stage_name ? node.workflow_stage_status : null,
  );
  const blockedCounts = countBy(rowsForFacet(rows, filters, "blockedOn"), (node) => node.blocked_on);
  const set = (patch: Partial<WorkItemsFilters>) => onChange({ ...filters, ...patch });

  return (
    <div className="wi-filters" role="group" aria-label="Filter work items">
      <Select aria-label="Workspace" value={filters.workspace} onChange={(event) => set({ workspace: event.target.value })}>
        <option value="all">All workspaces ({totalCount})</option>
        {workspaces.map(([slug, count]) => (
          <option key={slug} value={slug}>
            {slug} ({count})
          </option>
        ))}
      </Select>
      <FilterDropdown<TicketState>
        title="Status"
        allLabel="Any"
        options={TICKET_STATE_OPTIONS.map((id) => ({ id, label: TICKET_STATE_LABELS[id], count: stateCounts.get(id) ?? 0 }))}
        selected={filters.states}
        onToggle={(id) => set({ states: toggle(filters.states, id) })}
        matchWidth={false}
        onClear={() => set({ states: [] })}
      />
      <FilterDropdown<WorkItemType>
        title="Type"
        allLabel="Any"
        options={TYPE_FILTERS.map((option) => ({ ...option, count: typeCounts.get(option.id) ?? 0 }))}
        selected={filters.types}
        onToggle={(id) => set({ types: toggle(filters.types, id) })}
        matchWidth={false}
        onClear={() => set({ types: [] })}
      />
      <FilterDropdown<string>
        title="Priority"
        allLabel="Any"
        options={PRIORITY_OPTIONS.map((p) => ({ id: String(p), label: `P${p}`, count: prioCounts.get(p) ?? 0 }))}
        selected={filters.priorities.map(String)}
        onToggle={(id) => set({ priorities: toggle(filters.priorities, Number(id)).sort((a, b) => a - b) })}
        matchWidth={false}
        onClear={() => set({ priorities: [] })}
      />
      <FilterDropdown<StageStatus>
        title="Stage"
        allLabel="Any"
        options={STAGE_STATUS_OPTIONS.map((id) => ({ id, label: stageStatusLabel(id), count: stageCounts.get(id) ?? 0 }))}
        selected={filters.stageStatuses}
        onToggle={(id) => set({ stageStatuses: toggle(filters.stageStatuses, id) })}
        matchWidth={false}
        onClear={() => set({ stageStatuses: [] })}
      />
      <FilterDropdown<BlockedOn>
        title="Blocked on"
        allLabel="Anything"
        options={BLOCKED_ON_FILTERS.map((option) => ({ ...option, count: blockedCounts.get(option.id) ?? 0 }))}
        selected={filters.blockedOn}
        onToggle={(id) => set({ blockedOn: toggle(filters.blockedOn, id) })}
        matchWidth={false}
        onClear={() => set({ blockedOn: [] })}
      />
      <Button
        variant="secondary"
        compact
        aria-pressed={filters.topLevelOnly}
        className={filters.topLevelOnly ? "wi-toggle--on" : undefined}
        onClick={() => set({ topLevelOnly: !filters.topLevelOnly })}
      >
        Top level only
      </Button>
    </div>
  );
}

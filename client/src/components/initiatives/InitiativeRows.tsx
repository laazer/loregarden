import { useState } from "react";

import type { InitiativeMilestone } from "../../api/client";
import type { TicketState } from "../../api/types";
import { TICKET_STATE_COLORS, ticketStateChip, ticketStateLabel } from "../../lib/ticketStates";
import { peekTicket } from "../../state/ticketPeekStore";
import { OverflowMenu, OverflowMenuItem } from "../OverflowMenu";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { StatusChip } from "../ui/StatusChip";
import { MoveToMilestone } from "./SprintWorkControls";

/** Past this many rows a card groups them under their milestone, with counts; past this many
 * groups, the quiet ones wait behind a "Show more". */
export const GROUP_ROWS_AT = 12;
/** Past this many rows a card also gets a filter beside the list. */
export const FILTER_ROWS_AT = 50;

const RESOLVED: ReadonlySet<TicketState> = new Set(["done", "wont_do"]);
/** States an operator acts on, in the order a group summary names them. */
const ATTENTION: readonly TicketState[] = ["blocked", "in_progress", "parked"];

export interface RowActions {
  milestoneChoices: InitiativeMilestone[];
  busyId: string | null;
  onDetach: (milestoneId: string) => void;
  onMoveWork: (ticketId: string, parentId: string) => void;
}

/** Where a row lives; a member also names the milestone it stays under, unless its group already does. */
function placeLabel(m: InitiativeMilestone, grouped: boolean): string {
  const kind = m.work_item_type === "milestone" && !m.member ? "" : `${m.work_item_type} · `;
  const home = m.home_milestone && !grouped ? `in ${m.home_milestone} · ` : "";
  return `${kind}${home}${m.workspace_slug}`;
}

function MilestoneRow({
  milestone,
  grouped,
  actions,
}: {
  milestone: InitiativeMilestone;
  /** Its group's heading already says where it lives and that it is tracked. */
  grouped: boolean;
  actions: RowActions;
}) {
  const busy = actions.busyId === milestone.id;
  return (
    <li className="initiative-milestone">
      <span
        className="tree-state-dot"
        style={{ background: TICKET_STATE_COLORS[milestone.state] }}
        aria-hidden
      />
      <Button
        variant="plain"
        className="initiative-milestone-link"
        title={`Open ${milestone.external_id}`}
        onClick={() => peekTicket(milestone.id)}
      >
        <span className="initiative-mono">{milestone.external_id}</span> {milestone.title}
      </Button>
      <span className="initiative-ws-pill">{placeLabel(milestone, grouped)}</span>
      <StatusChip {...ticketStateChip(milestone.state)} />
      {milestone.member ? (
        grouped ? null : (
          <span className="initiative-muted" title="Tracked without moving it: remove it from Plan & board">
            Tracked
          </span>
        )
      ) : milestone.work_item_type === "milestone" ? (
        // Detach is the row's only action and the destructive one, so it sits
        // behind the menu rather than repeating as a button on every row.
        busy ? (
          <span className="initiative-muted" role="status">
            Detaching…
          </span>
        ) : (
          <OverflowMenu label={`${milestone.external_id} actions`}>
            <OverflowMenuItem onSelect={() => peekTicket(milestone.id)}>Open {milestone.external_id}</OverflowMenuItem>
            <OverflowMenuItem
              danger
              title="It stays a milestone in its workspace; only its link to this initiative goes"
              onSelect={() => actions.onDetach(milestone.id)}
            >
              Detach from this initiative
            </OverflowMenuItem>
          </OverflowMenu>
        )
      ) : (
        <MoveToMilestone
          item={milestone}
          milestones={actions.milestoneChoices}
          busy={busy}
          onMove={(milestoneId) => actions.onMoveWork(milestone.id, milestoneId)}
        />
      )}
    </li>
  );
}

interface RowGroup {
  key: string;
  label: string;
  tracked: boolean;
  rows: InitiativeMilestone[];
  /** How many rows sit in each attention state; empty when none need anyone. */
  attention: [TicketState, number][];
}

function groupKey(m: InitiativeMilestone): string {
  if (m.home_milestone) return m.home_milestone;
  return m.work_item_type === "milestone" && !m.member ? "Attached milestones" : "Not under a milestone";
}

/** Groups needing attention first, then the biggest, then by name. */
function groupRows(rows: InitiativeMilestone[]): RowGroup[] {
  const byKey = new Map<string, InitiativeMilestone[]>();
  for (const row of rows) {
    const key = groupKey(row);
    byKey.set(key, [...(byKey.get(key) ?? []), row]);
  }
  const groups = [...byKey].map(([key, members]): RowGroup => ({
    key,
    label: key,
    tracked: members.every((m) => m.member),
    rows: members,
    attention: ATTENTION.map((s): [TicketState, number] => [s, members.filter((m) => m.state === s).length]).filter(
      ([, n]) => n > 0,
    ),
  }));
  return groups.sort(
    (a, b) =>
      Number(b.attention.length > 0) - Number(a.attention.length > 0) ||
      b.rows.length - a.rows.length ||
      a.label.localeCompare(b.label, undefined, { numeric: true }),
  );
}

function matches(m: InitiativeMilestone, needle: string): boolean {
  return [m.external_id, m.title, m.home_milestone].some((field) => field.toLowerCase().includes(needle));
}

function RowGroupSection({
  group,
  open,
  onToggle,
  actions,
}: {
  group: RowGroup;
  open: boolean;
  onToggle: (open: boolean) => void;
  actions: RowActions;
}) {
  const attention = group.attention.map(([state, n]) => `${n} ${ticketStateLabel(state).toLowerCase()}`).join(" · ");
  return (
    <li className="initiative-group">
      <details open={open} onToggle={(e) => onToggle(e.currentTarget.open)}>
        <summary className="initiative-group-summary">
          <span className="initiative-mono">{group.label}</span>
          <span className="initiative-group-count">
            {group.rows.length} {group.rows.length === 1 ? "item" : "items"}
          </span>
          {attention ? <span className="initiative-group-attention">{attention}</span> : null}
          {group.tracked ? <span className="initiative-muted">tracked</span> : null}
        </summary>
        {open ? (
          <ul className="initiative-milestones initiative-group-rows">
            {group.rows.map((m) => (
              <MilestoneRow key={m.id} milestone={m} grouped actions={actions} />
            ))}
          </ul>
        ) : null}
      </details>
    </li>
  );
}

/**
 * A card's rows at volume: grouped under the milestone each lives in, with the
 * groups holding blocked or in-progress work open and first, resolved rows
 * behind a toggle, quiet groups past a dozen behind another, and a filter once
 * there are too many rows to scan.
 */
function GroupedRows({ title, rows, actions }: { title: string; rows: InitiativeMilestone[]; actions: RowActions }) {
  const [showResolved, setShowResolved] = useState(false);
  const [showAllGroups, setShowAllGroups] = useState(false);
  const [filter, setFilter] = useState("");
  // A group's open state, once the operator has toggled it; untouched groups follow the
  // default. A new filter clears it, so every group the filter matched in opens.
  const [toggled, setToggled] = useState<Map<string, boolean>>(() => new Map());

  const resolvedCount = rows.filter((m) => RESOLVED.has(m.state)).length;
  const needle = filter.trim().toLowerCase();
  const visible = rows.filter((m) => (showResolved || !RESOLVED.has(m.state)) && (!needle || matches(m, needle)));
  const groups = groupRows(visible);
  // A filter has already narrowed the list, and a group with work in flight is never hidden.
  const capped = groups.filter((g, n) => n < GROUP_ROWS_AT || g.attention.length > 0);
  const quietCount = needle ? 0 : groups.length - capped.length;
  const shown = needle || showAllGroups ? groups : capped;
  return (
    <div className="initiative-grouped">
      <div className="initiative-grouped-tools">
        {rows.length > FILTER_ROWS_AT ? (
          <Input
            type="search"
            className="btn-secondary filter-select initiative-input initiative-grouped-filter"
            aria-label={`Filter ${title} by id, title or milestone`}
            placeholder="Filter by id, title or milestone"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setToggled(new Map());
            }}
          />
        ) : null}
        {resolvedCount > 0 ? (
          <Button
            variant="secondary"
            compact
            aria-pressed={showResolved}
            onClick={() => setShowResolved((value) => !value)}
          >
            {showResolved ? "Hide" : "Show"} {resolvedCount} resolved
          </Button>
        ) : null}
        <span className="initiative-muted" role="status">
          {needle ? `${visible.length} of ${rows.length} match` : `${groups.length} ${groups.length === 1 ? "group" : "groups"}`}
        </span>
      </div>
      {groups.length === 0 ? (
        <p className="initiative-hint">
          {needle ? (
            <>
              Nothing here matches “{filter.trim()}”.{" "}
              <Button
                variant="plain"
                className="initiative-link-btn"
                onClick={() => {
                  setFilter("");
                  setToggled(new Map());
                }}
              >
                Clear the filter
              </Button>
            </>
          ) : (
            `All ${resolvedCount} are resolved.`
          )}
        </p>
      ) : (
        <ul className="initiative-milestones" aria-label={`${title} by milestone`}>
          {shown.map((group) => (
            <RowGroupSection
              key={group.key}
              group={group}
              // A filter opens every group it matched in; otherwise groups needing someone start open.
              open={toggled.get(group.key) ?? (needle !== "" || group.attention.length > 0)}
              onToggle={(open) => {
                setToggled((prev) => (prev.get(group.key) === open ? prev : new Map(prev).set(group.key, open)));
              }}
              actions={actions}
            />
          ))}
        </ul>
      )}
      {quietCount > 0 ? (
        <Button
          variant="plain"
          className="initiative-link-btn initiative-grouped-more"
          aria-expanded={showAllGroups}
          onClick={() => setShowAllGroups((value) => !value)}
        >
          {showAllGroups ? "Show fewer milestones" : `Show ${quietCount} more milestones`}
        </Button>
      ) : null}
    </div>
  );
}

export function InitiativeRows({
  title,
  rows,
  actions,
}: {
  title: string;
  rows: InitiativeMilestone[];
  actions: RowActions;
}) {
  // Grouping one group adds a heading and nothing else: a card of attached milestones stays flat.
  if (rows.length > GROUP_ROWS_AT && new Set(rows.map(groupKey)).size > 1) {
    return <GroupedRows title={title} rows={rows} actions={actions} />;
  }
  return (
    <ul className="initiative-milestones">
      {rows.map((m) => (
        <MilestoneRow key={m.id} milestone={m} grouped={false} actions={actions} />
      ))}
    </ul>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../../api/client";
import type { MilestoneSchedule } from "../../../api/initiativeApi";
import type { TicketState } from "../../../api/types";
import { planQueryKey } from "../../../hooks/useInitiativePlanner";
import { TICKET_STATE_LABELS } from "../../../lib/ticketStates";
import { boardQueryKey, buildColumns, milestoneOf } from "../../../lib/initiativeBoard";
import { describeError } from "../../../state/toastStore";
import { KanbanColumns } from "../../chat/primitives/KanbanPrimitive";
import { Button } from "../../ui/Button";
import { Select } from "../../ui/Select";

/** Where an operator may move a card. The server's state machine has the last word. */
const MOVE_TARGETS: TicketState[] = ["backlog", "in_progress", "blocked", "parked", "done", "wont_do"];

/**
 * Every work item under the initiative, across its workspaces, by state.
 *
 * Answers "what is moving, and what is stuck?" for one goal rather than one
 * repository — and the forecasts next door are made of exactly these cards
 * closing. Milestones themselves are the schedule's rows, not cards here.
 */
export function InitiativeBoard({
  initiativeId,
  milestones,
}: {
  initiativeId: string;
  milestones: MilestoneSchedule[];
}) {
  const qc = useQueryClient();
  const [milestoneFilter, setMilestoneFilter] = useState("");
  const [workspaceFilter, setWorkspaceFilter] = useState("");

  const board = useQuery({
    queryKey: boardQueryKey(initiativeId),
    queryFn: () => api.tickets({ ancestor_ticket_id: initiativeId }),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const move = useMutation({
    meta: { errorTitle: "Move ticket" },
    mutationFn: ({ id, state }: { id: string; state: TicketState }) => api.updateTicket(id, { state }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: boardQueryKey(initiativeId) });
      // A close changes remaining work, and so every forecast behind it.
      void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) });
    },
  });

  const milestoneIds = useMemo(() => new Set(milestones.map((m) => m.id)), [milestones]);
  const workspaces = useMemo(
    () => [...new Set(milestones.map((m) => m.workspace_slug))].sort(),
    [milestones],
  );

  const workItems = useMemo(() => {
    const tickets = (board.data ?? []).filter((t) => t.work_item_type !== "milestone");
    const owner = milestoneOf(board.data ?? [], milestoneIds);
    return tickets.filter(
      (t) =>
        (!milestoneFilter || owner.get(t.id) === milestoneFilter) &&
        (!workspaceFilter || t.workspace_slug === workspaceFilter),
    );
  }, [board.data, milestoneIds, milestoneFilter, workspaceFilter]);

  const filtered = Boolean(milestoneFilter || workspaceFilter);

  return (
    <section className="plan-board" aria-label="Initiative board">
      <div className="plan-board-filters">
        <Select
          aria-label="Filter the board by milestone"
          className="filter-select"
          value={milestoneFilter}
          onChange={(e) => setMilestoneFilter(e.target.value)}
        >
          <option value="">All milestones</option>
          {milestones.map((m) => (
            <option key={m.id} value={m.id}>
              {m.external_id} — {m.title}
            </option>
          ))}
        </Select>
        {workspaces.length > 1 ? (
          <Select
            aria-label="Filter the board by workspace"
            className="filter-select"
            value={workspaceFilter}
            onChange={(e) => setWorkspaceFilter(e.target.value)}
          >
            <option value="">All workspaces</option>
            {workspaces.map((slug) => (
              <option key={slug} value={slug}>
                {slug}
              </option>
            ))}
          </Select>
        ) : null}
        <span className="plan-muted" aria-live="polite">
          {board.isSuccess ? `${workItems.length} work item${workItems.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>

      {board.isPending ? (
        <div className="plan-skeleton plan-skeleton-board" aria-busy="true" aria-label="Loading the board" />
      ) : board.isError ? (
        <div className="plan-empty" role="alert">
          <p>The board could not be loaded: {describeError(board.error)}</p>
          <Button variant="secondary" compact onClick={() => void board.refetch()}>
            Retry
          </Button>
        </div>
      ) : workItems.length === 0 ? (
        <div className="plan-empty">
          <p>
            {filtered
              ? "Nothing matches these filters."
              : "No work items under this initiative's milestones yet. Scope a milestone in Ticket Studio and its tickets appear here."}
          </p>
          {filtered ? (
            <Button
              variant="secondary"
              compact
              onClick={() => {
                setMilestoneFilter("");
                setWorkspaceFilter("");
              }}
            >
              Clear filters
            </Button>
          ) : null}
        </div>
      ) : (
        <KanbanColumns
          columns={buildColumns(workItems)}
          tileAction={(ticket) => (
            <Select
              className="plan-move-select"
              aria-label={`Move ${ticket.title} to another state`}
              value=""
              disabled={move.isPending && move.variables?.id === ticket.id}
              onChange={(e) => {
                const state = e.target.value as TicketState;
                if (state) move.mutate({ id: ticket.id, state });
              }}
            >
              <option value="">Move to…</option>
              {MOVE_TARGETS.filter((s) => s !== ticket.state).map((s) => (
                <option key={s} value={s}>
                  {TICKET_STATE_LABELS[s]}
                </option>
              ))}
            </Select>
          )}
        />
      )}
    </section>
  );
}

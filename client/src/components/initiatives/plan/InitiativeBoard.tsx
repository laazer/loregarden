import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../../api/client";
import type { MilestoneSchedule, PlanNode } from "../../../api/initiativeApi";
import type { TicketState } from "../../../api/types";
import { planQueryKey } from "../../../hooks/useInitiativePlanner";
import { boardQueryKey, buildColumns, milestoneOf } from "../../../lib/initiativeBoard";
import { TICKET_STATE_LABELS } from "../../../lib/ticketStates";
import { describeError, pushToast } from "../../../state/toastStore";
import { KanbanColumns } from "../../chat/primitives/KanbanPrimitive";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { Select } from "../../ui/Select";

/** Where an operator may move cards. The server's state machine has the last word. */
const MOVE_TARGETS: TicketState[] = ["backlog", "in_progress", "blocked", "parked", "done", "wont_do"];

function tileHint(node: PlanNode | undefined): string {
  if (!node) return "";
  if (node.status === "needs_person") return "For a person";
  if (node.status === "ready") return "Ready";
  if (node.status === "waiting") return `Waiting on ${node.waiting_on.length}`;
  return "";
}

/**
 * Every work item under the initiative, across its workspaces, by state.
 *
 * Answers "what is moving, and what is stuck?" for one goal rather than one
 * repository. Cards are picked with a checkbox and acted on together from one
 * toolbar — move, mark for a person, start — so the board is not a wall of
 * controls, and an agent driving the UI finds one named control per action.
 */
export function InitiativeBoard({
  initiativeId,
  milestones,
  nodes,
  onNeedsPerson,
  onStart,
}: {
  initiativeId: string;
  milestones: MilestoneSchedule[];
  nodes: PlanNode[];
  onNeedsPerson: (ticketIds: string[], needsPerson: boolean) => Promise<unknown>;
  onStart: (ticketIds: string[]) => Promise<Record<string, string>>;
}) {
  const qc = useQueryClient();
  const [milestoneFilter, setMilestoneFilter] = useState("");
  const [workspaceFilter, setWorkspaceFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState<Set<TicketState>>(() => new Set());

  const board = useQuery({
    queryKey: boardQueryKey(initiativeId),
    queryFn: () => api.tickets({ ancestor_ticket_id: initiativeId }),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: boardQueryKey(initiativeId) });
    // A close changes remaining work, and so every forecast behind it.
    void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) });
  };

  const move = useMutation({
    meta: { errorTitle: "Move tickets" },
    // One at a time: each is a PATCH that re-derives its parent's rollup.
    mutationFn: async ({ ids, state }: { ids: string[]; state: TicketState }) => {
      for (const id of ids) await api.updateTicket(id, { state });
    },
    onSuccess: () => setSelected(new Set()),
    onSettled: refresh,
  });

  const act = useMutation({
    meta: { errorTitle: "Update tickets" },
    mutationFn: (run: () => Promise<unknown>) => run(),
    onSuccess: () => setSelected(new Set()),
    onSettled: refresh,
  });

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const milestoneIds = useMemo(() => new Set(milestones.map((m) => m.id)), [milestones]);
  const workspaces = useMemo(() => [...new Set(milestones.map((m) => m.workspace_slug))].sort(), [milestones]);

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
  const ids = [...selected].filter((id) => workItems.some((t) => t.id === id));
  const busy = move.isPending || act.isPending;
  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const start = () =>
    act.mutate(async () => {
      const outcome = await onStart(ids);
      const refused = Object.entries(outcome).filter(([, result]) => result !== "queued");
      if (refused.length > 0) {
        pushToast({
          tone: "warning",
          title: `${refused.length} of ${ids.length} not started`,
          message: refused.map(([id, result]) => `${nodeById.get(id)?.external_id ?? id}: ${result}`).join("\n"),
        });
      }
    });

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

      {ids.length > 0 ? (
        <div className="plan-board-toolbar" role="toolbar" aria-label="Selected tickets">
          <span>{ids.length} selected</span>
          <Select
            aria-label="Move the selected tickets to"
            value=""
            disabled={busy}
            onChange={(e) => {
              const state = e.target.value as TicketState;
              if (state) move.mutate({ ids, state });
            }}
          >
            <option value="">Move to…</option>
            {MOVE_TARGETS.map((s) => (
              <option key={s} value={s}>
                {TICKET_STATE_LABELS[s]}
              </option>
            ))}
          </Select>
          <Button variant="secondary" compact disabled={busy} onClick={() => act.mutate(() => onNeedsPerson(ids, true))}>
            Mark for a person
          </Button>
          <Button variant="secondary" compact disabled={busy} onClick={() => act.mutate(() => onNeedsPerson(ids, false))}>
            Agent can do it
          </Button>
          <Button variant="primary" compact disabled={busy} onClick={start}>
            Start now
          </Button>
          <Button variant="plain" className="plan-inline-btn" disabled={busy} onClick={() => setSelected(new Set())}>
            Clear selection
          </Button>
        </div>
      ) : null}

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
          columns={buildColumns(workItems, expanded)}
          onShowAll={(status) => setExpanded((current) => new Set(current).add(status))}
          tileAction={(ticket) => (
            <label className="plan-tile-select">
              <Input
                type="checkbox"
                checked={selected.has(ticket.id)}
                onChange={() => toggle(ticket.id)}
                aria-label={`Select ${ticket.title}`}
              />
              <span className="plan-muted">{tileHint(nodeById.get(ticket.id))}</span>
            </label>
          )}
        />
      )}
    </section>
  );
}

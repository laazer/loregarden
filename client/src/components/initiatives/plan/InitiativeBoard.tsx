import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";

import { api } from "../../../api/client";
import type { MilestoneSchedule, PlanNode } from "../../../api/initiativeApi";
import type { TicketState, TicketSummary, WaitingOnRef } from "../../../api/types";
import { planQueryKey } from "../../../hooks/useInitiativePlanner";
import { usePolledReadFailure } from "../../../hooks/usePolledReadFailure";
import { boardQueryKey, buildColumns, milestoneOf } from "../../../lib/initiativeBoard";
import { TICKET_STATE_LABELS, type BoardColumnKey } from "../../../lib/ticketStates";
import { describeError, pushToast } from "../../../state/toastStore";
import { KanbanColumns } from "../../chat/primitives/KanbanPrimitive";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { Select } from "../../ui/Select";
import { StaleReadNotice } from "../../ui/StaleReadNotice";
import { WaitingOnLinks } from "../../waiting/WaitingOnLinks";

/** Where an operator may move cards. The server's state machine has the last word. */
const MOVE_TARGETS: TicketState[] = ["backlog", "in_progress", "blocked", "parked", "done", "wont_do"];

function tileHint(
  node: PlanNode | undefined,
  ticket: TicketSummary,
  byId: Map<string, TicketSummary>,
): ReactNode {
  if (!node) return null;
  if (node.status === "needs_person") return "For a person";
  if (node.status === "ready") return "Ready";
  // The tile already names what it waits on when the list can see it. The graph
  // also holds on a done prerequisite not landed, which the list reads as met:
  // name those too, rather than a count nothing on the board explains.
  if (node.status === "waiting" && !ticket.waiting_on?.length) {
    const refs: WaitingOnRef[] = node.waiting_on.flatMap((id) => {
      const prereq = byId.get(id);
      return prereq
        ? [
            {
              id,
              external_id: prereq.external_id,
              title: `${prereq.title} — done, not landed`,
              state: prereq.state,
              reason: "prerequisite" as const,
              blocked_on: null,
              workspace_slug: prereq.workspace_slug,
            },
          ]
        : [];
    });
    if (refs.length === 0) return `Waiting on ${node.waiting_on.length}`;
    return (
      <>
        Not landed: <WaitingOnLinks refs={refs} here={ticket.workspace_slug} shown={2} />
      </>
    );
  }
  return null;
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
  const [expanded, setExpanded] = useState<Set<BoardColumnKey>>(() => new Set());

  const board = useQuery({
    queryKey: boardQueryKey(initiativeId),
    queryFn: () => api.tickets({ ancestor_ticket_id: initiativeId }),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  // `(board.data?.length ?? 0) > 0`, NOT `workItems.length > 0`: `workItems` is
  // this read filtered by milestone and workspace, so the filtered reading
  // would replace a legitimate "Nothing matches these filters." view and its
  // Clear-filters button with the first-load error pane whenever the operator
  // had filtered to a milestone holding nothing. The question is what the READ
  // produced. A read that succeeded with zero rows and then failed still takes
  // the pane, which is the same answer the filtered reading wanted.
  const failure = usePolledReadFailure(board, initiativeId, (board.data?.length ?? 0) > 0);

  // Scoped to the operator's own click, never to `board.isFetching`: the pane
  // now survives its own refetch, so an `isFetching` busy state would disable
  // the one control on screen and read "Retrying…" every 30 seconds for the
  // background poll — dead at the moment it is reached for.
  const [retrying, setRetrying] = useState(false);
  const retry = () => {
    setRetrying(true);
    // `refetch()` resolves rather than rejecting; the reason is rendered from
    // the held failure by the pane around this button.
    void board.refetch().finally(() => setRetrying(false));
  };

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
  const ticketsById = useMemo(() => new Map((board.data ?? []).map((t) => [t.id, t])), [board.data]);
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
          {/* Content-based, never the read's success status, which a failed
              refetch over good data makes false: the count would blank out on a
              poll that lost nothing. */}
          {workItems.length > 0 ? `${workItems.length} work item${workItems.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>

      {/* Qualifies the figures below without displacing a control: a sibling
          between two block elements, so the board shifts down and nothing moves
          sideways. `role="status"`, because this appears on a 30s timer. */}
      {failure.stale && (
        <StaleReadNotice
          lead="These work items may be out of date — the last refresh failed."
          error={failure.stale.error}
          fallback="The initiative's tickets could not be read."
          testId="board-refresh-failed"
        />
      )}

      {workItems.length > 0 ? (
        // Always on screen, so the checkboxes say what they are for before anyone ticks one.
        <div className="plan-board-toolbar" role="toolbar" aria-label="Selected tickets">
          <span className={ids.length > 0 ? undefined : "plan-muted"} aria-live="polite">
            {ids.length > 0
              ? `${ids.length} selected`
              : "Tick tickets to move, start, or mark them for a person together."}
          </span>
          <Select
            aria-label="Move the selected tickets to"
            value=""
            disabled={busy || ids.length === 0}
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
          <Button
            variant="secondary"
            compact
            disabled={busy || ids.length === 0}
            onClick={() => act.mutate(() => onNeedsPerson(ids, true))}
          >
            Mark for a person
          </Button>
          <Button
            variant="secondary"
            compact
            disabled={busy || ids.length === 0}
            onClick={() => act.mutate(() => onNeedsPerson(ids, false))}
          >
            Agent can do it
          </Button>
          <Button variant="primary" compact disabled={busy || ids.length === 0} onClick={start}>
            Start now
          </Button>
          {ids.length > 0 ? (
            <Button variant="plain" className="plan-inline-btn" disabled={busy} onClick={() => setSelected(new Set())}>
              Clear selection
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* The held failure wins over the pending state, because `refetch()` on a
          query with no data puts it BACK to `status: "pending"` — so a chain
          that tested `isPending` first would unmount this pane into the
          skeleton on the operator's own Retry and take the reason with it. */}
      {failure.blocking ? (
        <div className="plan-empty" role="alert">
          <p>The board could not be loaded: {describeError(failure.blocking.error)}</p>
          <Button variant="secondary" compact disabled={retrying} onClick={retry}>
            {retrying ? "Retrying…" : "Retry"}
          </Button>
        </div>
      ) : board.isPending ? (
        <div className="plan-skeleton plan-skeleton-board" aria-busy="true" aria-label="Loading the board" />
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
          tileAction={(ticket) => {
            const hint = tileHint(nodeById.get(ticket.id), ticket, ticketsById);
            return (
              <div className="plan-tile-foot">
                <label className="plan-tile-select">
                  <Input
                    type="checkbox"
                    checked={selected.has(ticket.id)}
                    onChange={() => toggle(ticket.id)}
                    aria-label={`Select ${ticket.title}`}
                  />
                  Select
                </label>
                {hint ? <span className="plan-tile-hint">{hint}</span> : null}
              </div>
            );
          }}
        />
      )}
    </section>
  );
}

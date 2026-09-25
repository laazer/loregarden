/**
 * The ticket board beside the queue: what is waiting, running and stuck.
 *
 * The queue board above it answers "which runs hold a slot"; this answers
 * "which work is those runs' reason", which is the question the page could not
 * answer without leaving it. It draws the same columns and tiles as the chat
 * kanban primitive — `KanbanColumns` is shared, not copied.
 *
 * Bounded on purpose. One list request per column with a `limit`, and the true
 * column size from the status summary, rather than one unfiltered read of every
 * ticket: this control plane holds ~1000 of them and the queue page is already
 * on a socket. A column that holds more than it drew says so — a silently
 * truncated board is a board that disagrees with the count above it.
 */

import { useQueries, useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TicketState, TicketStatusSummary, TicketSummary } from "../api/types";
import { KanbanColumns, type KanbanColumn } from "./chat/primitives/KanbanPrimitive";
import { PrimitiveCard } from "./chat/primitives/PrimitiveCard";
import { describeError } from "../state/toastStore";

/** Work in flight, left to right. Closed states are deliberately absent: the
 *  queue page is about what is still to run. */
const COLUMNS: TicketState[] = ["backlog", "in_progress", "blocked"];

/** Tiles per column. The count in the header is the real total either way. */
const PER_COLUMN = 8;

const REFRESH_MS = 30_000;

export function QueueTicketBoard() {
  const summary = useQuery({
    queryKey: ["tickets", "status-summary", "queue-board"],
    queryFn: () => api.ticketStatusSummary(),
    staleTime: 10_000,
    refetchInterval: REFRESH_MS,
  });

  const columnQueries = useQueries({
    queries: COLUMNS.map((state) => ({
      queryKey: ["tickets", "queue-board", state, PER_COLUMN],
      queryFn: () => api.tickets({ state, limit: PER_COLUMN }),
      staleTime: 10_000,
      refetchInterval: REFRESH_MS,
    })),
  });

  const loading = summary.isLoading || columnQueries.some((q) => q.isLoading);
  // A column that could not be read must not render as a column with no work in
  // it. One message for the board, naming the first failure.
  const failure =
    summary.error ?? columnQueries.find((q) => q.error)?.error ?? null;

  const columns: KanbanColumn[] = COLUMNS.map((status, index) => ({
    status,
    tickets: (columnQueries[index]?.data ?? []) as TicketSummary[],
    total: summary.data ? summary.data[status as keyof TicketStatusSummary] : undefined,
  }));

  const totalOpen = summary.data
    ? COLUMNS.reduce((sum, status) => sum + summary.data[status as keyof TicketStatusSummary], 0)
    : null;

  return (
    <PrimitiveCard
      title="Ticket board"
      subtitle={
        totalOpen === null
          ? "Work still to run"
          : totalOpen === 0
            ? "Nothing open — every ticket is closed or parked"
            : `${totalOpen} open across ${COLUMNS.length} states`
      }
      loading={loading}
      error={failure ? describeError(failure, "Could not load the ticket board") : null}
      collapsible
    >
      <KanbanColumns columns={columns} />
    </PrimitiveCard>
  );
}

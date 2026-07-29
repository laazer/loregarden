import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { TicketState, TicketSummary } from "../../../api/types";
import { ticketStateColor } from "../../../lib/ticketStates";
import type { FilterableKanbanPart, KanbanPart, StatusColumnPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";

const DEFAULT_STATUSES: TicketState[] = [
  "backlog",
  "in_progress",
  "blocked",
  "done",
  "wont_do",
];

/** Resolving the listed ids costs one small request each; the unfiltered ticket list is
 *  megabytes and seconds, so only fall back to it when the card names no ids. */
function useTicketBucket(ticketIds: string[] | undefined) {
  const ids = ticketIds ?? [];
  return useQuery({
    queryKey: ["tickets", "kanban", ids],
    queryFn: async () => {
      if (!ids.length) return api.tickets({});
      const settled = await Promise.allSettled(ids.map((id) => api.ticket(id)));
      return settled.flatMap<TicketSummary>((r) =>
        r.status === "fulfilled" ? [r.value] : [],
      );
    },
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

function TicketMini({ ticket }: { ticket: TicketSummary }) {
  return (
    <div className="lg-primitive-ticket-row">
      <span
        className="lg-primitive-card-dot"
        style={{ background: ticketStateColor(ticket.state), marginTop: 0 }}
      />
      <span className="lg-primitive-ticket-row-title">{ticket.title}</span>
      <OpenTicketButton ticketId={ticket.id} compact label={`Open ${ticket.title}`} />
    </div>
  );
}

export function StatusColumnPrimitive({ part }: { part: StatusColumnPart }) {
  const { data, isLoading, error } = useTicketBucket(part.ticket_ids);
  const tickets = (data ?? []).filter((t) => t.state === part.status);

  return (
    <PrimitiveCard
      title={part.title ?? part.status.replace("_", " ")}
      subtitle={`${tickets.length} tickets`}
      loading={isLoading}
      error={error ? (error instanceof Error ? error.message : "Failed to load") : null}
    >
      <div className="lg-primitive-kanban-col">
        {tickets.map((t) => (
          <TicketMini key={t.id} ticket={t} />
        ))}
      </div>
    </PrimitiveCard>
  );
}

export function KanbanPrimitive({
  part,
}: {
  part: KanbanPart | FilterableKanbanPart;
}) {
  const filterable = part.primitive === "filterable_kanban";
  const statuses = (part.statuses?.length ? part.statuses : DEFAULT_STATUSES) as TicketState[];
  const filters =
    filterable && "filters" in part && part.filters?.length
      ? (part.filters as TicketState[])
      : statuses;
  const [active, setActive] = useState<Set<string>>(
    () => new Set(filterable ? filters : statuses),
  );
  const { data, isLoading, error } = useTicketBucket(part.ticket_ids);

  const columns = useMemo(() => {
    const tickets = data ?? [];
    return statuses
      .filter((s) => active.has(s))
      .map((status) => ({
        status,
        tickets: tickets.filter((t) => t.state === status),
      }));
  }, [data, statuses, active]);

  return (
    <PrimitiveCard
      title={part.title ?? (filterable ? "Filterable board" : "Kanban")}
      loading={isLoading}
      error={error ? (error instanceof Error ? error.message : "Failed to load") : null}
    >
      {filterable ? (
        <div className="lg-primitive-filters" role="group" aria-label="Status filters">
          {filters.map((status) => (
            <button
              key={status}
              type="button"
              aria-pressed={active.has(status)}
              onClick={() =>
                setActive((prev) => {
                  const next = new Set(prev);
                  if (next.has(status)) next.delete(status);
                  else next.add(status);
                  return next;
                })
              }
            >
              {status.replace("_", " ")}
            </button>
          ))}
        </div>
      ) : null}
      <div className="lg-primitive-kanban">
        {columns.map((col) => (
          <div key={col.status} className="lg-primitive-kanban-col">
            <p className="lg-primitive-kanban-col-title">
              {col.status.replace("_", " ")} · {col.tickets.length}
            </p>
            {col.tickets.map((t) => (
              <TicketMini key={t.id} ticket={t} />
            ))}
          </div>
        ))}
      </div>
    </PrimitiveCard>
  );
}

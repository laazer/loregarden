import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { TicketState, TicketSummary } from "../../../api/types";
import { ticketStateColor, ticketStateLabel } from "../../../lib/ticketStates";
import type { FilterableKanbanPart, KanbanPart, StatusColumnPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { TicketCardBody, stageProgressSegments } from "./TicketCardMeta";

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

/** Board tile: the v6 list card compacted to a column's width. */
function TicketMini({ ticket }: { ticket: TicketSummary }) {
  const progress = stageProgressSegments(ticket.stages);

  return (
    <div className="lg-primitive-ticket-tile">
      <TicketCardBody
        compact
        title={ticket.title}
        priority={ticket.priority}
        state={ticket.state}
        workspaceSlug={ticket.workspace_slug}
        stageName={ticket.workflow_stage_name || undefined}
        stageStatus={ticket.workflow_stage_status}
        segments={progress.segments}
        progressLabel={progress.total ? `${progress.done}/${progress.total}` : null}
      />
      <div className="lg-primitive-ticket-tile-action">
        <OpenTicketButton ticketId={ticket.id} compact label={`Open ${ticket.title}`} />
      </div>
    </div>
  );
}

export function StatusColumnPrimitive({ part }: { part: StatusColumnPart }) {
  const { data, isLoading, error } = useTicketBucket(part.ticket_ids);
  const tickets = (data ?? []).filter((t) => t.state === part.status);

  return (
    <PrimitiveCard
      title={part.title ?? ticketStateLabel(part.status)}
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
              {ticketStateLabel(status)}
            </button>
          ))}
        </div>
      ) : null}
      <div className="lg-primitive-kanban">
        {columns.map((col) => (
          <div key={col.status} className="lg-primitive-kanban-col">
            <p className="lg-primitive-kanban-col-title">
              <span
                className="lg-primitive-ticket-state-dot"
                style={{ background: ticketStateColor(col.status) }}
                aria-hidden
              />
              {ticketStateLabel(col.status)}
              <span className="lg-primitive-kanban-col-count">{col.tickets.length}</span>
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

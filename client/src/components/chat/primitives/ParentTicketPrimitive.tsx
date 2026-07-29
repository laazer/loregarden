import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { ParentTicketPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { childProgressPercent, ticketStateColor } from "./ticketProgress";
import { ticketIsRunning, useRunControls } from "./useRunControls";

export function ParentTicketPrimitive({ part }: { part: ParentTicketPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticket", part.ticket_id],
    queryFn: () => api.ticket(part.ticket_id),
    refetchInterval: (q) =>
      ticketIsRunning(q.state.data?.workflow_stage_status) ? 1000 : 5000,
  });
  const childrenQuery = useQuery({
    queryKey: ["tickets", "children", part.ticket_id],
    queryFn: () => api.tickets({ parent_ticket_id: part.ticket_id }),
    enabled: Boolean(part.ticket_id),
    refetchInterval: 5000,
  });
  const controls = useRunControls(part.ticket_id);
  const children = childrenQuery.data ?? [];
  const percent = childProgressPercent(children);
  const running = ticketIsRunning(data?.workflow_stage_status);

  return (
    <PrimitiveCard
      title={data?.title ?? part.title ?? "Parent ticket"}
      subtitle={data ? `${data.external_id} · ${children.length} children` : undefined}
      statusDot={ticketStateColor(data?.state)}
      loading={isLoading}
      error={error ? (error instanceof Error ? error.message : "Failed to load") : null}
      tone={running ? "accent" : "default"}
      actions={
        <>
          <OpenTicketButton ticketId={part.ticket_id} />
          {running ? (
            <button
              type="button"
              className="lg-primitive-run-btn lg-primitive-run-btn--stop"
              disabled={controls.isStopping}
              onClick={() => void controls.stop()}
            >
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="lg-primitive-run-btn lg-primitive-run-btn--play"
              disabled={controls.isStarting || !data}
              onClick={() => void controls.start()}
            >
              Play
            </button>
          )}
        </>
      }
    >
      <div className="lg-primitive-progress" aria-label={`Progress ${percent}%`}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <p className="lg-primitive-card-sub">{percent}% complete</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
        {children.map((child) => (
          <div key={child.id} className="lg-primitive-ticket-row">
            <span
              className="lg-primitive-card-dot"
              style={{ background: ticketStateColor(child.state), marginTop: 0 }}
            />
            <span className="lg-primitive-ticket-row-title">{child.title}</span>
            <span style={{ color: "var(--txl)", fontSize: 11 }}>{child.state}</span>
            <OpenTicketButton ticketId={child.id} compact label={`Open ${child.title}`} />
          </div>
        ))}
      </div>
    </PrimitiveCard>
  );
}

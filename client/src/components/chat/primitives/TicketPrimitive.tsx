import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { TicketPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { ticketIsRunning, useRunControls } from "./useRunControls";
import { ticketStateColor } from "./ticketProgress";

export function TicketPrimitive({ part }: { part: TicketPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticket", part.ticket_id],
    queryFn: () => api.ticket(part.ticket_id),
    refetchInterval: (q) =>
      ticketIsRunning(q.state.data?.workflow_stage_status) ? 1000 : 5000,
  });
  const controls = useRunControls(part.ticket_id);
  const running = ticketIsRunning(data?.workflow_stage_status);

  return (
    <PrimitiveCard
      title={data?.title ?? part.title ?? part.ticket_id}
      subtitle={data ? `${data.external_id} · ${data.work_item_type}` : undefined}
      statusDot={ticketStateColor(data?.state)}
      meta={
        data ? (
          <>
            <span>{data.state.replace("_", " ")}</span>
            <span>{data.workflow_stage_name || data.workflow_stage_key || "—"}</span>
            <span>P{data.priority}</span>
            {data.workspace_slug ? <span>{data.workspace_slug}</span> : null}
          </>
        ) : null
      }
      loading={isLoading}
      error={error ? (error instanceof Error ? error.message : "Failed to load ticket") : null}
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
      {data?.description ? <p className="lg-primitive-card-sub">{data.description}</p> : null}
    </PrimitiveCard>
  );
}

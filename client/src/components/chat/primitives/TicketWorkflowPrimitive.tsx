import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { WorkflowStageTimeline } from "../../WorkflowStageTimeline";
import type { TicketWorkflowPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { PlayButton, StopButton } from "./RunControlButton";
import { ticketIsRunning, useRunControls } from "./useRunControls";

export function TicketWorkflowPrimitive({ part }: { part: TicketWorkflowPart }) {
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
      title={data?.title ?? part.title ?? "Ticket workflow"}
      subtitle={data?.external_id}
      loading={isLoading}
      error={error ? (error instanceof Error ? error.message : "Failed to load") : null}
      tone={running ? "accent" : "default"}
      resourceAction={<OpenTicketButton ticketId={part.ticket_id} />}
      actions={
        running ? (
          <StopButton disabled={controls.isStopping} onClick={() => void controls.stop()} />
        ) : (
          <PlayButton
            disabled={controls.isStarting || !data}
            onClick={() => void controls.start()}
          />
        )
      }
    >
      {data ? (
        <WorkflowStageTimeline
          stages={data.stages}
          currentStageKey={data.workflow_stage_key}
        />
      ) : null}
    </PrimitiveCard>
  );
}

import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { WorkflowStageTimeline } from "../../WorkflowStageTimeline";
import type { TicketWorkflowPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { PlayButton, StopButton } from "./RunControlButton";
import { TicketCardBody, stageProgressSegments } from "./TicketCardMeta";
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
  const progress = stageProgressSegments(data?.stages);
  const title = data?.title ?? part.title ?? "Ticket workflow";

  return (
    <PrimitiveCard
      className="lg-primitive-ticket-card lg-primitive-ticket-card--workflow"
      title={title}
      header={<span className="lg-primitive-ticket-card-spacer" aria-hidden />}
      loading={isLoading}
      error={error ? (error instanceof Error ? error.message : "Failed to load") : null}
      tone={running ? "accent" : "default"}
      resourceAction={<OpenTicketButton ticketId={part.ticket_id} compact />}
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
        <>
          <TicketCardBody
            title={title}
            priority={data.priority}
            state={data.state}
            workspaceSlug={data.workspace_slug}
            stageName={data.workflow_stage_name || undefined}
            stageStatus={data.workflow_stage_status}
            segments={progress.segments}
            progressLabel={progress.total ? `${progress.done}/${progress.total}` : null}
          />
          <div className="lg-primitive-ticket-timeline">
            <WorkflowStageTimeline
              stages={data.stages}
              currentStageKey={data.workflow_stage_key}
            />
          </div>
        </>
      ) : null}
    </PrimitiveCard>
  );
}

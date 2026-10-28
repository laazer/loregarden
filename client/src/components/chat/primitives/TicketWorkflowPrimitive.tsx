import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../../api/client";
import type { WorkflowStageView } from "../../../api/types";
import { Button } from "../../ui/Button";
import { WorkflowStageTimeline } from "../../WorkflowStageTimeline";
import type { TicketWorkflowPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { PlayButton, StopButton } from "./RunControlButton";
import { TicketPriorityBadge, stageProgressSegments } from "./TicketCardMeta";
import { ticketStateChip } from "../../../lib/ticketStates";
import { ticketQueryRetry, ticketRefetchInterval } from "./ticketLiveQuery";
import { ticketIsRunning, useRunControls } from "./useRunControls";

export function TicketWorkflowPrimitive({ part }: { part: TicketWorkflowPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticket", part.ticket_id],
    queryFn: () => api.ticket(part.ticket_id),
    retry: ticketQueryRetry,
    refetchInterval: ticketRefetchInterval,
  });
  const controls = useRunControls(part.ticket_id);
  const running = ticketIsRunning(data?.workflow_stage_status);
  const progress = stageProgressSegments(data?.stages);
  const title = data?.title ?? part.title ?? "Ticket workflow";
  const loadError = error
    ? error instanceof Error
      ? error.message
      : "Failed to load"
    : null;
  const live = Boolean(data) && !loadError;

  return (
    <PrimitiveCard
      className="lg-primitive-ticket-card--workflow"
      title={title}
      kind="ticket_workflow"
      lead={data ? <TicketPriorityBadge priority={data.priority} /> : null}
      refLine={data ? `${data.external_id} · ${data.workspace_slug}` : part.ticket_id}
      status={data ? ticketStateChip(data.state) : null}
      copyRef={data?.external_id ?? part.ticket_id}
      footerHint={
        live ? (running ? "Stop pauses the running stage" : `${progress.done} of ${progress.total} stages done`) : null
      }
      loading={isLoading}
      error={loadError}
      resourceAction={live ? <OpenTicketButton ticketId={part.ticket_id} compact /> : null}
      actions={
        live ? (
          running ? (
            <StopButton disabled={controls.isStopping} onClick={() => void controls.stop()} />
          ) : (
            <PlayButton
              disabled={controls.isStarting}
              onClick={() => void controls.start()}
            />
          )
        ) : null
      }
    >
      {data ? <StageWindow stages={data.stages} currentStageKey={data.workflow_stage_key} /> : null}
    </PrimitiveCard>
  );
}

/** Stages shown around the current one before the rest fold away. */
const STAGE_WINDOW = 5;

/**
 * The ticket's stages on the vine, five at a time: the one before the current
 * stage, the current one and what follows, with the rest a click away.
 */
function StageWindow({ stages, currentStageKey }: { stages: WorkflowStageView[]; currentStageKey: string }) {
  const listId = useId();
  const [all, setAll] = useState(false);
  const current = Math.max(0, stages.findIndex((s) => s.key === currentStageKey));
  const start = Math.max(0, Math.min(current - 1, stages.length - STAGE_WINDOW));
  const folded = !all && stages.length > STAGE_WINDOW;
  const shown = folded ? stages.slice(start, start + STAGE_WINDOW) : stages;
  return (
    <div className="lg-primitive-ticket-timeline" id={listId}>
      <WorkflowStageTimeline stages={shown} currentStageKey={currentStageKey} />
      {stages.length > STAGE_WINDOW ? (
        <Button
          variant="plain"
          className="lg-primitive-stage-more"
          aria-expanded={all}
          aria-controls={listId}
          onClick={() => setAll(!all)}
        >
          {all ? "Show fewer stages" : `Show all ${stages.length} stages`}
        </Button>
      ) : null}
    </div>
  );
}

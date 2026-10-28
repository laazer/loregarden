import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { TicketPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { PlayButton, StopButton } from "./RunControlButton";
import { TicketCardBody, TicketPriorityBadge, stageProgressSegments } from "./TicketCardMeta";
import { ticketStateChip } from "../../../lib/ticketStates";
import { ticketQueryRetry, ticketRefetchInterval } from "./ticketLiveQuery";
import { ticketIsRunning, useRunControls } from "./useRunControls";

export function TicketPrimitive({ part }: { part: TicketPart }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticket", part.ticket_id],
    queryFn: () => api.ticket(part.ticket_id),
    retry: ticketQueryRetry,
    refetchInterval: ticketRefetchInterval,
  });
  const controls = useRunControls(part.ticket_id);
  const running = ticketIsRunning(data?.workflow_stage_status);
  const progress = stageProgressSegments(data?.stages);
  const title = data?.title ?? part.title ?? part.ticket_id;
  const loadError = error
    ? error instanceof Error
      ? error.message
      : "Failed to load ticket"
    : !isLoading && !data
      ? "Ticket not found"
      : null;
  // Missing refs must not look runnable — Baxter sometimes invents ticket ids.
  const live = Boolean(data) && !loadError;

  return (
    <PrimitiveCard
      title={title}
      kind="ticket"
      lead={data ? <TicketPriorityBadge priority={data.priority} /> : null}
      refLine={data ? `${data.external_id} · ${data.workspace_slug}` : part.ticket_id}
      status={data ? ticketStateChip(data.state) : null}
      copyRef={data?.external_id ?? part.ticket_id}
      footerHint={live ? (running ? "Stop pauses the running stage" : "Run starts the next stage") : null}
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
      {data ? (
        <TicketCardBody
          headless
          title={title}
          priority={data.priority}
          state={data.state}
          workspaceSlug={data.workspace_slug}
          stageName={data.workflow_stage_name || undefined}
          stageStatus={data.workflow_stage_status}
          segments={progress.segments}
          progressLabel={progress.total ? `${progress.done}/${progress.total}` : null}
        />
      ) : null}
    </PrimitiveCard>
  );
}

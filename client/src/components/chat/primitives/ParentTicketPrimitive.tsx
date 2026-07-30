import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { TicketTree } from "../../TicketTree";
import type { ParentTicketPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { PlayButton, StopButton } from "./RunControlButton";
import {
  TicketCardBody,
  childProgressSegments,
  stageProgressSegments,
} from "./TicketCardMeta";
import { toTicketTreeNode } from "./ticketTreeNodes";
import { ticketIsRunning, useRunControls } from "./useRunControls";

/** Children arrive one level deep, so no row in this card is expandable. */
const NO_EXPANDED_ROWS: Set<string> = new Set();

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
  const children = useMemo(() => childrenQuery.data ?? [], [childrenQuery.data]);
  const nodes = useMemo(() => children.map(toTicketTreeNode), [children]);
  const running = ticketIsRunning(data?.workflow_stage_status);
  const title = data?.title ?? part.title ?? "Parent ticket";
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Parents report children done/total; fall back to stage progress when none yet.
  const progress = children.length
    ? childProgressSegments(children)
    : stageProgressSegments(data?.stages);

  return (
    <PrimitiveCard
      className="lg-primitive-ticket-card lg-primitive-ticket-card--parent"
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
      ) : null}
      {nodes.length ? (
        <div className="lg-primitive-ticket-children lg-primitive-ticket-list--v6">
          <TicketTree
            nodes={nodes}
            selectedId={selectedId}
            expandedIds={NO_EXPANDED_ROWS}
            onSelect={setSelectedId}
            onToggle={setSelectedId}
            showExternalId
            presentation="v6"
            renderRowAction={(node) => (
              <OpenTicketButton ticketId={node.id} compact label={`Open ${node.title}`} />
            )}
          />
        </div>
      ) : null}
    </PrimitiveCard>
  );
}
